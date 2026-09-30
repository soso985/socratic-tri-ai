"""task-6（Owner 直接指令）· 写文件锁 ``control.write_lock`` 的单元测试（新语义）。

**语义变化**：状态来源由「调用方传入」改为**唯一权威文件**
``data_root()/agent_state/task_state.json`` 的 ``tasks.<task_id>.status``。
调用方传入的 ``status``/``state``/``current_state`` **一律忽略**；任何不可用情形 fail-closed。

两条关键用例（Owner 点名必须有）：
* :func:`test_lying_caller_is_denied` —— 权威 ``BLOCKED`` + 调用方传 ``{"status":"RUNNING"}`` → **必须拒绝**；
* :func:`test_allows_when_authoritative_status_is_running` —— 权威 ``RUNNING`` → **必须放行**。

隔离：``CONTROL_DATA_ROOT`` 指向 ``tmp_path``，测试通过造临时 ``task_state.json`` 控制权威状态，
因此**不会**触碰真实 ``team/agent_state/task_state.json``，也不会向真实审计日志写入。
"""

from __future__ import annotations

import builtins
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from control import ENV_DATA_ROOT, objection, write_lock  # noqa: E402
from control.write_lock import (  # noqa: E402
    REQUIRED_STATE,
    StateSourceError,
    WriteDenied,
    locked_write,
    read_authoritative_status,
    task_state_path,
)

_ORIGINAL = "# 原内容：一个字节都不许变\n"
_TASK = "TASK-001"


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path, monkeypatch):
    """把权威状态文件与审计日志都重定向到临时目录。"""
    monkeypatch.setenv(ENV_DATA_ROOT, str(tmp_path))
    return tmp_path


@pytest.fixture()
def target(tmp_path):
    path = tmp_path / "protected" / "artifact.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_ORIGINAL, encoding="utf-8")
    return path


def _set_authoritative(root: Path, tasks=None, *, raw=None) -> Path:
    """造权威状态文件。``tasks=None`` 时默认给 ``TASK-001: BLOCKED``。"""
    path = root / "agent_state" / "task_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
    else:
        payload = {"schema": "task-internal/v1",
                   "tasks": tasks if tasks is not None else {_TASK: {"status": "BLOCKED"}}}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _audit_lines(root: Path):
    log = root / "agent_state" / "audit_log.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


# ================================================================ 拒绝路径


def test_denies_when_authoritative_status_is_blocked(target, isolated_data_root):
    _set_authoritative(isolated_data_root, {_TASK: {"status": "BLOCKED"}})
    with pytest.raises(WriteDenied) as excinfo:
        locked_write(target, "篡改内容", task_id=_TASK)
    assert excinfo.value.state == "BLOCKED"
    assert excinfo.value.required_state == REQUIRED_STATE
    assert excinfo.value.state_source == str(task_state_path())


def test_lying_caller_is_denied(target, isolated_data_root):
    """★ 关键用例（撒谎）：权威 BLOCKED，调用方传 RUNNING → **必须拒绝**。"""
    _set_authoritative(isolated_data_root, {_TASK: {"status": "BLOCKED"}})
    before = target.read_bytes()
    before_mtime = target.stat().st_mtime_ns

    with pytest.raises(WriteDenied) as excinfo:
        locked_write(target, "调用方声称自己可以写\n", task_id=_TASK,
                     task_state={"status": "RUNNING"})

    assert target.read_bytes() == before          # 一个字节都没变
    assert target.stat().st_mtime_ns == before_mtime
    assert excinfo.value.state == "BLOCKED"       # 报的是权威状态，不是调用方声称的
    message = str(excinfo.value)
    assert "忽略" in message                      # 明确告知传入值被忽略
    assert "权威文件" in message

    records = _audit_lines(isolated_data_root)    # 审计记下了拒绝
    assert len(records) == 1
    event = records[0]["event"]
    assert event["kind"] == "write_lock.denied"
    assert event["state"] == "BLOCKED"
    assert event["caller_supplied_state_ignored"] is True
    assert "RUNNING" in event["caller_supplied_task_state"]
    assert event["bytes_written"] == 0
    assert excinfo.value.audit_hash == records[0]["hash"]


@pytest.mark.parametrize("caller_state", [
    {"status": "RUNNING"},
    {"state": "RUNNING"},
    {"current_state": "RUNNING"},
    "RUNNING",
    {"status": "RUNNING", "state": "RUNNING", "current_state": "RUNNING"},
])
def test_all_caller_supplied_state_spellings_are_ignored(target, isolated_data_root, caller_state):
    """调用方所有"假装"写法的键名（status/state/current_state）一律不参与判定。"""
    _set_authoritative(isolated_data_root, {_TASK: {"status": "BLOCKED"}})
    with pytest.raises(WriteDenied):
        locked_write(target, "篡改", task_id=_TASK, task_state=caller_state)
    assert target.read_text(encoding="utf-8") == _ORIGINAL


def test_denies_when_authoritative_file_missing(target, isolated_data_root):
    with pytest.raises(WriteDenied) as excinfo:
        locked_write(target, "篡改", task_id=_TASK, task_state={"status": "RUNNING"})
    assert "不存在" in str(excinfo.value)
    assert excinfo.value.state is None
    assert "忽略" in str(excinfo.value)
    assert len(_audit_lines(isolated_data_root)) == 1


@pytest.mark.parametrize("raw", [
    "{ this is not json",
    "",
    "[]",
    '"just a string"',
])
def test_denies_when_authoritative_file_unreadable_or_malformed(target, isolated_data_root, raw):
    _set_authoritative(isolated_data_root, raw=raw)
    with pytest.raises(WriteDenied):
        locked_write(target, "篡改", task_id=_TASK, task_state={"status": "RUNNING"})
    assert target.read_text(encoding="utf-8") == _ORIGINAL


def test_denies_when_tasks_object_missing(target, isolated_data_root):
    _set_authoritative(isolated_data_root, raw='{"schema": "task-internal/v1"}')
    with pytest.raises(WriteDenied) as excinfo:
        locked_write(target, "篡改", task_id=_TASK)
    assert "tasks" in str(excinfo.value)


def test_denies_when_task_id_not_in_authoritative_file(target, isolated_data_root):
    _set_authoritative(isolated_data_root, {"TASK-999": {"status": "RUNNING"}})
    with pytest.raises(WriteDenied) as excinfo:
        locked_write(target, "篡改", task_id=_TASK, task_state={"status": "RUNNING"})
    assert "TASK-001" in str(excinfo.value)
    assert target.read_text(encoding="utf-8") == _ORIGINAL


@pytest.mark.parametrize("record", [
    {},
    {"status": ""},
    {"status": "   "},
    {"status": 123},
    {"status": None},
    {"status": ["RUNNING"]},
    "not-a-dict",
])
def test_denies_when_status_missing_or_invalid(target, isolated_data_root, record):
    _set_authoritative(isolated_data_root, {_TASK: record})
    with pytest.raises(WriteDenied):
        locked_write(target, "篡改", task_id=_TASK)


@pytest.mark.parametrize("bad_task_id", ["", "   ", None, 123, ["TASK-001"]])
def test_denies_when_task_id_is_not_a_valid_selector(target, isolated_data_root, bad_task_id):
    _set_authoritative(isolated_data_root, {_TASK: {"status": "RUNNING"}})
    before = target.read_bytes()
    with pytest.raises(WriteDenied):
        locked_write(target, "篡改", task_id=bad_task_id, task_state={"status": "RUNNING"})
    assert target.read_bytes() == before


def test_denial_never_opens_target(target, isolated_data_root, monkeypatch):
    """行为实证：拒绝路径**从不**对目标调用 ``open()``。"""
    _set_authoritative(isolated_data_root, {_TASK: {"status": "BLOCKED"}})
    opened = []
    real_open = builtins.open

    def spy(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)
    with pytest.raises(WriteDenied):
        locked_write(target, "篡改", task_id=_TASK, task_state={"status": "RUNNING"})
    assert str(target) not in opened
    assert str(target.resolve()) not in opened


def test_denial_creates_no_temp_file(target, isolated_data_root):
    _set_authoritative(isolated_data_root, {_TASK: {"status": "BLOCKED"}})
    before = sorted(p.name for p in target.parent.iterdir())
    with pytest.raises(WriteDenied):
        locked_write(target, "篡改", task_id=_TASK)
    assert sorted(p.name for p in target.parent.iterdir()) == before


def test_denial_record_names_state_source_and_ignored_caller_value(target, isolated_data_root):
    _set_authoritative(isolated_data_root, {_TASK: {"status": "未批准"}})
    with pytest.raises(WriteDenied):
        locked_write(target, "篡改", task_id=_TASK, task_state={"status": "RUNNING"})
    event = _audit_lines(isolated_data_root)[0]["event"]
    assert event["state_source"] == str(isolated_data_root / "agent_state" / "task_state.json")
    assert event["state"] == "未批准"
    assert event["caller_supplied_state_ignored"] is True
    assert "RUNNING" in event["caller_supplied_task_state"]
    assert event["task_id"] == _TASK
    assert "选择器" in event["task_id_role"]


def test_denial_message_states_caller_value_is_ignored(target, isolated_data_root):
    _set_authoritative(isolated_data_root, {_TASK: {"status": "BLOCKED"}})
    with pytest.raises(WriteDenied) as excinfo:
        locked_write(target, "篡改", task_id=_TASK, task_state={"status": "RUNNING"})
    message = str(excinfo.value)
    assert "忽略" in message
    assert "status/state/current_state" in message


def test_denial_message_notes_when_no_caller_state_passed(target, isolated_data_root):
    _set_authoritative(isolated_data_root, {_TASK: {"status": "BLOCKED"}})
    with pytest.raises(WriteDenied) as excinfo:
        locked_write(target, "篡改", task_id=_TASK)
    assert "即便传入也会被忽略" in str(excinfo.value)


def test_denial_records_accumulate_in_chain(target, isolated_data_root):
    _set_authoritative(isolated_data_root, {_TASK: {"status": "BLOCKED"}})
    for _ in range(2):
        with pytest.raises(WriteDenied):
            locked_write(target, "篡改", task_id=_TASK)
    lines = _audit_lines(isolated_data_root)
    assert len(lines) == 2
    assert lines[1]["prev_hash"] == lines[0]["hash"]


# ================================================================ 放行路径


def test_allows_when_authoritative_status_is_running(target, isolated_data_root):
    """★ 关键用例（放行）：权威 RUNNING → **必须放行**（证明锁不是"一律拒绝"）。"""
    _set_authoritative(isolated_data_root, {_TASK: {"status": "RUNNING"}})
    result = locked_write(target, "# 新内容\n", task_id=_TASK)
    assert target.read_text(encoding="utf-8") == "# 新内容\n"
    assert result["bytes"] == len("# 新内容\n".encode("utf-8"))
    assert result["sha256"] == hashlib.sha256("# 新内容\n".encode("utf-8")).hexdigest()
    assert result["state"] == "RUNNING"
    assert result["task_id"] == _TASK


def test_caller_claiming_blocked_does_not_prevent_allowed_write(target, isolated_data_root):
    """反向证明：调用方传 BLOCKED，但权威是 RUNNING → 仍放行（传入值两个方向都被忽略）。"""
    _set_authoritative(isolated_data_root, {_TASK: {"status": "RUNNING"}})
    locked_write(target, "allowed\n", task_id=_TASK, task_state={"status": "BLOCKED"})
    assert target.read_text(encoding="utf-8") == "allowed\n"


def test_task_id_selects_the_right_record(tmp_path, isolated_data_root):
    """多任务文件：task_id 决定读哪一条，而不是提供状态。"""
    _set_authoritative(isolated_data_root, {
        "TASK-001": {"status": "BLOCKED"},
        "TASK-002": {"status": "RUNNING"},
    })
    denied = tmp_path / "denied.txt"
    allowed = tmp_path / "allowed.txt"
    with pytest.raises(WriteDenied):
        locked_write(denied, "x", task_id="TASK-001", task_state={"status": "RUNNING"})
    locked_write(allowed, "y", task_id="TASK-002", task_state={"status": "BLOCKED"})
    assert not denied.exists()
    assert allowed.read_text(encoding="utf-8") == "y"


def test_allowed_write_uses_atomic_replace(target, isolated_data_root, monkeypatch):
    _set_authoritative(isolated_data_root, {_TASK: {"status": "RUNNING"}})
    calls = []
    real_replace = os.replace

    def spy(src, dst, *args, **kwargs):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", spy)
    locked_write(target, "# 原子写入\n", task_id=_TASK)
    assert len(calls) == 1
    src, dst = calls[0]
    assert dst == str(target)
    assert Path(src).parent == target.parent  # 同目录 → 同卷原子性
    assert not Path(src).exists()
    assert [p.name for p in target.parent.iterdir()] == [target.name]


def test_allowed_write_creates_missing_parent_directories(tmp_path, isolated_data_root):
    _set_authoritative(isolated_data_root, {_TASK: {"status": "RUNNING"}})
    nested = tmp_path / "a" / "b" / "c.txt"
    locked_write(nested, "x", task_id=_TASK)
    assert nested.read_text(encoding="utf-8") == "x"


def test_allowed_write_accepts_bytes_content(target, isolated_data_root):
    _set_authoritative(isolated_data_root, {_TASK: {"status": "RUNNING"}})
    locked_write(target, b"bytes-content", task_id=_TASK)
    assert target.read_bytes() == b"bytes-content"


# ================================================================ 接口与约定


def test_task_id_is_a_required_keyword(target, isolated_data_root):
    """task_id 是必填参数——忘了传不能变成"默认放行"。"""
    _set_authoritative(isolated_data_root, {_TASK: {"status": "RUNNING"}})
    with pytest.raises(TypeError):
        locked_write(target, "x")  # type: ignore[call-arg]


def test_read_authoritative_status_returns_running(isolated_data_root):
    _set_authoritative(isolated_data_root, {_TASK: {"status": "RUNNING"}})
    assert read_authoritative_status(_TASK) == "RUNNING"


def test_read_authoritative_status_raises_on_unusable_source(isolated_data_root):
    with pytest.raises(StateSourceError):
        read_authoritative_status(_TASK)


def test_state_source_path_matches_objection_convention(isolated_data_root):
    """权威文件路径必须与 ``objection._task_state_path()`` 是同一约定（同一文件）。"""
    assert task_state_path() == objection._task_state_path()
    assert task_state_path().name == "task_state.json"
    assert task_state_path().parent.name == "agent_state"


def test_exempt_comment_is_documented_in_source():
    """Owner 要求：拒绝记录写审计日志豁免于本锁，必须在注释里写明（可机验）。"""
    source = Path(write_lock.__file__).read_text(encoding="utf-8")
    assert "豁免" in source
    assert "鸡生蛋" in source


def test_selector_vs_state_assertion_is_documented_in_source():
    """Owner 要求：docstring 里写明 task_id 是选择器、不是状态断言。"""
    source = Path(write_lock.__file__).read_text(encoding="utf-8")
    assert "选择器" in source
    assert "状态断言" in source
    assert "一律忽略" in source


def test_module_does_not_import_or_patch_other_writers():
    """不得给 audit_log / objection / dual_judge 接线，也不得反向依赖 gate。"""
    source = Path(write_lock.__file__).read_text(encoding="utf-8")
    for forbidden in ("import gate", "from .gate", "import objection", "from .objection",
                      "import dual_judge", "from .dual_judge"):
        assert forbidden not in source


def test_other_modules_are_untouched_by_this_task():
    """不得改 ``audit_log.py`` / ``objection.py`` / ``gate.py`` 一个字符。

    注：``dual_judge.py`` **自 task-8 起是授权变更对象**（§8.5 封存协议 commit→reveal），
    故从本守卫中移出；它当前的形态由 ``test_control.py`` 的 dual_judge 测试覆盖。
    本守卫仍然守着另外三个模块——这也是 task-6 时它能第一时间抓到 dual_judge 被改的原因。
    """
    expected = {
        "audit_log.py": "c7aab23807a6637b364bdbcc612d8a95503bfa868919f26617112542784473bc",
        "objection.py": "1328d36f847bbf9a3eb2e6fda2d58a8ac663dde173c6ebce4436874431426f09",
        "gate.py": "2c778d3c89502589763554000c547bb851a2ea71c0e5d01a5e557d27ba53c98a",
    }
    for name, digest in expected.items():
        actual = hashlib.sha256((_SRC / "control" / name).read_bytes()).hexdigest()
        assert actual == digest, f"{name} 被改动了"
