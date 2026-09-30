# -*- coding: utf-8 -*-
"""
Mentor 独立验收 · task-6：write_lock 状态源改为唯一权威文件（封 R-1）

通过标准（Owner）：未批准时，即使调用写成 {"status":"RUNNING"}，也必须抛错，
目标文件一个字节不变，审计记下拒绝。

不复用 Executor 的测试代码，独立构造。上一轮脚本 mentor-verify-writelock.py 按旧接口
（无 task_id）调用，在本版接口下会 TypeError —— 按"不抹掉历史证据"原则**保留原文件不改**。
"""
import builtins
import hashlib
import json
import os
import pathlib
import sys
import tempfile

SRC = pathlib.Path(r"E:\Socratic_M\team\src")
SNAP = pathlib.Path(r"E:\Socratic_M\team\evidence\src-snapshot-before-state-source.json")
REAL_TASK_STATE = pathlib.Path(r"E:\Socratic_M\team\agent_state\task_state.json")
REAL_AUDIT = pathlib.Path(r"E:\Socratic_M\team\agent_state\audit_log.jsonl")
TARGET = SRC / "control" / "gate.py"

sys.path.insert(0, str(SRC))
import control  # noqa: E402
from control import audit_log, write_lock  # noqa: E402

results = []


def rec(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def h(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def make_root(status_map, raw=None):
    """造一个隔离数据根；raw 不为 None 时直接写入原始文本（用于损坏场景）。"""
    root = pathlib.Path(tempfile.mkdtemp(prefix="mentor-ss-"))
    d = root / "agent_state"
    d.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        (d / "task_state.json").write_text(raw, encoding="utf-8")
    else:
        (d / "task_state.json").write_text(
            json.dumps({"schema": "task-internal/v1",
                        "tasks": {k: {"status": v} for k, v in status_map.items()}},
                       ensure_ascii=False), encoding="utf-8")
    return root


print("=" * 78)
print("Mentor 独立验收 · write_lock 状态源 = 唯一权威文件")
print("=" * 78)

# ============ A. 真实工作区：撒谎用例（Owner 点名的核心场景） ============
control.reset_data_root()          # 回到真实数据根
real_status = json.loads(REAL_TASK_STATE.read_text(encoding="utf-8"))["tasks"]["TASK-001"]["status"]
print(f"[信息] 真实权威状态 tasks.TASK-001.status = {real_status!r}")
rec("前提：真实权威状态非 RUNNING（未批准/BLOCKED）", real_status != "RUNNING", f"status={real_status}")

audit_before = len(REAL_AUDIT.read_text(encoding="utf-8").splitlines()) if REAL_AUDIT.exists() else 0
ts_before = h(REAL_TASK_STATE)
before_hash, before_stat = h(TARGET), TARGET.stat()

opened = []
_real_open = builtins.open


def _probe(file, *a, **kw):
    try:
        if pathlib.Path(str(file)).resolve() == TARGET.resolve():
            opened.append(str(file))
    except Exception:
        pass
    return _real_open(file, *a, **kw)


builtins.open = _probe
raised = None
try:
    write_lock.locked_write(TARGET, "TAMPERED VIA LYING CALLER\n",
                            task_id="TASK-001", task_state={"status": "RUNNING"})
except Exception as exc:  # noqa: BLE001
    raised = exc
finally:
    builtins.open = _real_open

after_hash, after_stat = h(TARGET), TARGET.stat()
rec("★ 撒谎用例：调用方传 {\"status\":\"RUNNING\"} → 抛错",
    raised is not None, f"{type(raised).__name__}" if raised else "!! 竟然放行")
rec("★ 通过标准：目标文件一个字节都不变", before_hash == after_hash,
    f"{before_hash[:16].upper()} -> {after_hash[:16].upper()}")
rec("目标字节数未变", before_stat.st_size == after_stat.st_size, f"{before_stat.st_size} bytes")
rec("目标 mtime 未变（证否「写了再回滚」）", before_stat.st_mtime_ns == after_stat.st_mtime_ns)
rec("拒绝路径从未打开目标文件（探针实测）", not opened, f"open: {opened}" if opened else "0 次")
rec("权威文件本身未被改动", ts_before == h(REAL_TASK_STATE))
rec("异常消息点明了调用方值被忽略",
    raised is not None and "忽略" in str(raised), str(raised)[:90] if raised else "")

audit_after_lines = REAL_AUDIT.read_text(encoding="utf-8").splitlines() if REAL_AUDIT.exists() else []
rec("审计日志追加了拒绝记录", len(audit_after_lines) == audit_before + 1,
    f"{audit_before} -> {len(audit_after_lines)}")
if audit_after_lines:
    e = json.loads(audit_after_lines[-1])
    ev = e.get("event", e)
    rec("拒绝记录写明权威状态", ev.get("state") == real_status, f"state={ev.get('state')!r}")
    rec("拒绝记录标明调用方值被忽略",
        ev.get("caller_supplied_state_ignored") is True,
        f"caller_supplied_state_ignored={ev.get('caller_supplied_state_ignored')!r}")
    rec("拒绝记录 bytes_written=0", ev.get("bytes_written") == 0, f"{ev.get('bytes_written')}")
    rec("拒绝记录可过链式校验", audit_log.verify(e.get("hash")) is True, f"hash={str(e.get('hash'))[:16]}")

# ============ B. 隔离数据根：放行 / 反向撒谎 / 选择器 / fail-closed ============
tmp_run = make_root({"TASK-001": "RUNNING"})
control.set_data_root(tmp_run)
try:
    out = write_lock.locked_write(tmp_run / "ok.txt", "hello", task_id="TASK-001",
                                  task_state={"status": "BLOCKED"})
    rec("反向撒谎：权威 RUNNING + 调用方传 BLOCKED → 仍放行（忽略是双向的）",
        (tmp_run / "ok.txt").read_text(encoding="utf-8") == "hello")
except Exception as exc:  # noqa: BLE001
    rec("反向撒谎 → 仍放行", False, f"{type(exc).__name__}: {exc}")

tmp_sel = make_root({"TASK-001": "BLOCKED", "TASK-002": "RUNNING"})
control.set_data_root(tmp_sel)
try:
    write_lock.locked_write(tmp_sel / "a.txt", "x", task_id="TASK-001")
    rec("选择器：TASK-001 为 BLOCKED → 拒绝", False, "竟然放行")
except write_lock.WriteDenied:
    rec("选择器：TASK-001 为 BLOCKED → 拒绝", True, "WriteDenied")
try:
    write_lock.locked_write(tmp_sel / "b.txt", "x", task_id="TASK-002")
    rec("选择器：TASK-002 为 RUNNING → 放行", (tmp_sel / "b.txt").exists())
except Exception as exc:  # noqa: BLE001
    rec("选择器：TASK-002 为 RUNNING → 放行", False, f"{type(exc).__name__}")

cases = [
    ("权威文件不存在", None, {"TASK-001": "RUNNING"}, "TASK-001"),
    ("JSON 损坏", "{not json", None, "TASK-001"),
    ("空文件", "", None, "TASK-001"),
    ("顶层不是对象", '["x"]', None, "TASK-001"),
    ("tasks 缺失", '{"schema":"x"}', None, "TASK-001"),
    ("task_id 不在其中", None, {"TASK-009": "RUNNING"}, "TASK-001"),
    ("status 缺失", None, {}, "TASK-001"),
    ("status 为空串", None, {"TASK-001": ""}, "TASK-001"),
    ("status 为 RUNNING 以外", None, {"TASK-001": "READY"}, "TASK-001"),
]
for label, raw, smap, tid in cases:
    root = make_root(smap or {}, raw=raw)
    if label == "权威文件不存在":
        (root / "agent_state" / "task_state.json").unlink()
    control.set_data_root(root)
    try:
        write_lock.locked_write(root / "z.txt", "x", task_id=tid)
        rec(f"fail-closed：{label} → 应拒绝", False, "竟然放行")
    except write_lock.WriteDenied:
        rec(f"fail-closed：{label} → 拒绝", True, "WriteDenied")
    except Exception as exc:  # noqa: BLE001
        rec(f"fail-closed：{label} → 拒绝", True, f"（{type(exc).__name__}）")

for label, tid in [("task_id 缺失（None）", None), ("task_id 为空串", "")]:
    control.set_data_root(make_root({"TASK-001": "RUNNING"}))
    try:
        write_lock.locked_write(TARGET, "x", task_id=tid)
        rec(f"fail-closed：{label} → 应拒绝", False, "竟然放行")
    except write_lock.WriteDenied:
        rec(f"fail-closed：{label} → 拒绝", True, "WriteDenied")
    except TypeError:
        rec(f"fail-closed：{label} → 拒绝", True, "TypeError（未提供）")

control.reset_data_root()

# ============ C. 卫生：其他模块与权威文件 ============
snap = json.loads(SNAP.read_text(encoding="utf-8"))["files"]
now = {str(p.relative_to(SRC)).replace("\\", "/"): h(p)
       for p in sorted(SRC.rglob("*")) if p.is_file() and "__pycache__" not in str(p)}
others = [k for k in snap if k != "control/write_lock.py"]
changed_others = [k for k in others if now.get(k) != snap[k]]
rec("除 write_lock.py 外，src 其他模块全部逐字节未变", not changed_others,
    f"被改: {changed_others}" if changed_others else f"{len(others)} 个文件全 MATCH")
rec("audit_log / objection / dual_judge / gate 未被接锁",
    all(now.get(k) == snap[k] for k in
        ["control/audit_log.py", "control/objection.py", "control/dual_judge.py", "control/gate.py"]))

print("=" * 78)
passed = sum(1 for _, ok, _ in results if ok)
print(f"Mentor 独立验收：{passed} / {len(results)} 项通过")
for n, ok, d in results:
    if not ok:
        print(f"  FAIL: {n} :: {d}")
print("=" * 78)
sys.exit(0 if passed == len(results) else 1)
