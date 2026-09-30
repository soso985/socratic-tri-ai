"""task-5（Owner 直接指令）· 写文件锁 ``control.write_lock`` 的单元测试。

覆盖点：
* 非 ``RUNNING`` 状态一律拒绝、目标**字节不变**；
* 拒绝路径**从不打开目标文件**（用 ``builtins.open`` 探针实证，不是靠读代码推断）；
* 拒绝路径**不产生任何临时文件**；
* 拒绝记录**豁免于本锁**——状态非 RUNNING 时审计日志仍被写入（鸡生蛋问题的正面证据）；
* ``RUNNING`` 时正常写入，且走**临时文件 + 原子替换**；
* 状态不可解析时 **fail-closed**（拒绝，而不是放行）。

隔离：数据根被重定向到 ``tmp_path``，因此本测试不会写 ``team/agent_state/``。
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

from control import ENV_DATA_ROOT, write_lock  # noqa: E402
from control.write_lock import REQUIRED_STATE, WriteDenied, locked_write, status_of  # noqa: E402

_ORIGINAL = "# 原内容：一个字节都不许变\n"


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_DATA_ROOT, str(tmp_path))
    return tmp_path


@pytest.fixture()
def target(tmp_path):
    path = tmp_path / "protected" / "artifact.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_ORIGINAL, encoding="utf-8")
    return path


def _audit_lines(root: Path):
    log = root / "agent_state" / "audit_log.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------- 拒绝路径


def test_locked_write_denies_when_state_not_running(target):
    with pytest.raises(WriteDenied) as excinfo:
        locked_write(target, "篡改内容", task_state={"status": "未批准"})
    assert excinfo.value.state == "未批准"
    assert excinfo.value.required_state == REQUIRED_STATE
    assert str(target) in str(excinfo.value)


def test_denial_leaves_target_byte_identical(target):
    before = target.read_bytes()
    before_mtime = target.stat().st_mtime_ns
    with pytest.raises(WriteDenied):
        locked_write(target, "篡改内容" * 100, task_state="READY")
    assert target.read_bytes() == before
    assert target.stat().st_mtime_ns == before_mtime  # 连 mtime 都没动


def test_denial_never_opens_target(target, monkeypatch):
    """行为实证：拒绝路径**从不**对目标调用 ``open()``。"""
    opened = []
    real_open = builtins.open

    def spy(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)
    with pytest.raises(WriteDenied):
        locked_write(target, "篡改内容", task_state={"status": "DRAFT"})
    assert str(target) not in opened
    assert str(target.resolve()) not in opened


def test_denial_creates_no_temp_file(target):
    before = sorted(p.name for p in target.parent.iterdir())
    with pytest.raises(WriteDenied):
        locked_write(target, "篡改内容", task_state={"status": "BLOCKED"})
    assert sorted(p.name for p in target.parent.iterdir()) == before


def test_denial_is_recorded_in_audit_log_despite_the_lock(target, isolated_data_root):
    """鸡生蛋问题的正面证据：状态非 RUNNING，审计日志仍写入了拒绝记录。"""
    assert _audit_lines(isolated_data_root) == []
    with pytest.raises(WriteDenied) as excinfo:
        locked_write(target, "篡改内容", task_state="未批准")
    lines = _audit_lines(isolated_data_root)
    assert len(lines) == 1
    event = lines[0]["event"]
    assert event["kind"] == "write_lock.denied"
    assert event["state"] == "未批准"
    assert event["bytes_written"] == 0
    assert event["path"] == str(target)
    # 异常对象回带该拒绝记录的链式哈希，便于外部审计定位
    assert excinfo.value.audit_hash == lines[0]["hash"]


def test_denial_record_survives_write_to_same_audit_file(target, isolated_data_root):
    """连续两次拒绝 → 审计日志两条、链式哈希相接（证明留痕可累积）。"""
    for _ in range(2):
        with pytest.raises(WriteDenied):
            locked_write(target, "篡改", task_state={"status": "未批准"})
    lines = _audit_lines(isolated_data_root)
    assert len(lines) == 2
    assert lines[1]["prev_hash"] == lines[0]["hash"]


# ---------------------------------------------------------------- 放行路径


def test_locked_write_allows_when_running(target):
    result = locked_write(target, "# 新内容\n", task_state={"status": "RUNNING"})
    assert target.read_text(encoding="utf-8") == "# 新内容\n"
    assert result["bytes"] == len("# 新内容\n".encode("utf-8"))
    assert result["sha256"] == hashlib.sha256("# 新内容\n".encode("utf-8")).hexdigest()


def test_allowed_write_uses_atomic_replace(target, monkeypatch):
    calls = []
    real_replace = os.replace

    def spy(src, dst, *args, **kwargs):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", spy)
    locked_write(target, "# 原子写入\n", task_state="RUNNING")
    assert len(calls) == 1
    src, dst = calls[0]
    assert dst == str(target)
    assert Path(src).parent == target.parent  # 临时文件与目标同目录，保证同卷原子性
    assert not Path(src).exists()  # 替换后临时文件不残留
    assert [p.name for p in target.parent.iterdir()] == [target.name]


def test_allowed_write_creates_missing_parent_directories(tmp_path):
    nested = tmp_path / "a" / "b" / "c.txt"
    locked_write(nested, "x", task_state={"status": "RUNNING"})
    assert nested.read_text(encoding="utf-8") == "x"


def test_allowed_write_accepts_bytes_content(target):
    locked_write(target, b"bytes-content", task_state="RUNNING")
    assert target.read_bytes() == b"bytes-content"


# ---------------------------------------------------------------- 状态解析与 fail-closed


def test_status_of_accepts_str_and_dict():
    assert status_of("RUNNING") == "RUNNING"
    assert status_of({"status": "READY"}) == "READY"
    assert status_of({"state": "BLOCKED"}) == "BLOCKED"
    assert status_of("  RUNNING  ") == "RUNNING"


@pytest.mark.parametrize("bad_state", [None, {}, {"status": ""}, {"other": "RUNNING"}, 123])
def test_unparsable_state_fails_closed(target, bad_state):
    """状态不可解析时**拒绝**（fail-closed），绝不"放行以求稳"。"""
    before = target.read_bytes()
    with pytest.raises(WriteDenied):
        locked_write(target, "篡改", task_state=bad_state)
    assert target.read_bytes() == before


def test_missing_task_state_argument_is_a_type_error(target):
    """``task_state`` 是必填的关键字参数——不能"忘了传"就默认放行。"""
    with pytest.raises(TypeError):
        locked_write(target, "篡改")  # type: ignore[call-arg]


def test_exempt_comment_is_documented_in_source():
    """Owner 要求：豁免设计点必须写在代码注释里（可机验）。"""
    source = Path(write_lock.__file__).read_text(encoding="utf-8")
    assert "豁免" in source
    assert "审计日志" in source


def test_write_lock_module_does_not_import_gate():
    """本锁不反向依赖 gate.py（gate 是判定器，锁是拦截器，二者解耦）。"""
    source = Path(write_lock.__file__).read_text(encoding="utf-8")
    assert "import gate" not in source
    assert "from .gate" not in source
