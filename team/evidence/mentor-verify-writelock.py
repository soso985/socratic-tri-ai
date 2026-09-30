# -*- coding: utf-8 -*-
"""
Mentor 独立验收 · task-5 写文件锁（Owner 直接指令）
通过标准（Owner 原话，唯一）：「那个文件的内容一个字节都不变」。

本脚本：
  1. 独立重跑篡改演示（不是读 Executor 的日志）
  2. 比对 team/src 全部文件 vs 动手前快照
  3. 用自己的探针验证"拒绝路径从未打开目标文件"
  4. 用临时数据根隔离，不污染真实 agent_state
"""
import builtins
import hashlib
import json
import os
import pathlib
import sys
import tempfile

SRC = pathlib.Path(r"E:\Socratic_M\team\src")
SNAP = pathlib.Path(r"E:\Socratic_M\team\evidence\src-snapshot-before-lock.json")
TMPROOT = pathlib.Path(tempfile.mkdtemp(prefix="mentor-writelock-"))
os.environ["CONTROL_DATA_ROOT"] = str(TMPROOT)

sys.path.insert(0, str(SRC))
from control import audit_log, write_lock  # noqa: E402

results = []


def rec(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def h(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


print("=" * 78)
print("Mentor 独立验收 · 写文件锁")
print("隔离数据根:", TMPROOT)
print("=" * 78)

# ---------- 1. 我自己发起篡改（选 gate.py，与 Executor 同一目标，便于对撞） ----------
TARGET = SRC / "control" / "gate.py"
before_hash = h(TARGET)
before_stat = TARGET.stat()
before_size = before_stat.st_size
before_mtime = before_stat.st_mtime_ns

# 探针：记录目标是否被 open 过
opened = []
_real_open = builtins.open


def _probe(file, *a, **kw):
    try:
        if pathlib.Path(str(file)).resolve() == TARGET.resolve():
            opened.append((str(file), kw.get("mode", a[0] if a else "r")))
    except Exception:
        pass
    return _real_open(file, *a, **kw)


builtins.open = _probe
raised = None
try:
    write_lock.locked_write(
        TARGET,
        "TAMPERED BY MENTOR INDEPENDENT VERIFICATION\n",
        task_state={"status": "未批准"},
    )
except Exception as exc:  # noqa: BLE001
    raised = exc
finally:
    builtins.open = _real_open

after_hash = h(TARGET)
after_stat = TARGET.stat()

rec("篡改尝试确实抛错（未被静默放行）", raised is not None,
    f"{type(raised).__name__}: {raised}" if raised else "!! 没有抛错")
rec("异常类型为 WriteDenied",
    type(raised).__name__ == "WriteDenied" if raised else False)
rec("★ 通过标准：目标文件内容一个字节都不变", before_hash == after_hash,
    f"{before_hash[:16].upper()} -> {after_hash[:16].upper()}")
rec("目标文件字节数未变", before_size == after_stat.st_size, f"{before_size} bytes")
rec("目标文件 mtime 未变（证否「写了再回滚」）", before_mtime == after_stat.st_mtime_ns)
rec("拒绝路径从未打开目标文件（探针实测）", len(opened) == 0,
    f"open 调用: {opened}" if opened else "0 次 open")

# ---------- 2. 审计日志是否记了拒绝 ----------
log_path = TMPROOT / "agent_state" / "audit_log.jsonl"
lines = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines() if x.strip()] \
    if log_path.exists() else []
denials = [r for r in lines if "denied" in json.dumps(r, ensure_ascii=False)]
rec("审计日志记录了拒绝", len(denials) >= 1, f"拒绝条目 {len(denials)} 条")
if denials:
    d = denials[-1].get("event", denials[-1])
    rec("拒绝记录含 bytes_written=0", d.get("bytes_written") == 0, f"bytes_written={d.get('bytes_written')}")
    hh = denials[-1].get("hash") or d.get("hash")
    if hh:
        rec("拒绝记录可通过链式校验", audit_log.verify(hh) is True, f"hash={str(hh)[:16]}")

# ---------- 3. team/src 全部文件 vs 动手前快照 ----------
snap = json.loads(SNAP.read_text(encoding="utf-8"))["files"]
now = {str(p.relative_to(SRC)).replace("\\", "/"): h(p)
       for p in sorted(SRC.rglob("*")) if p.is_file()}
changed = [(k, snap[k], now[k]) for k in snap if k in now and now[k] != snap[k]]
removed = [k for k in snap if k not in now]
added = [k for k in now if k not in snap]
rec("快照内 5 个既有文件全部逐字节未变", not changed and not removed,
    f"被改 {len(changed)} / 删除 {len(removed)}")
for k, a, b in changed:
    print(f"      !! {k}: {a[:16]} -> {b[:16]}")
rec("新增文件仅 write_lock.py（其余为测试与证据）",
    set(added) <= {"control/write_lock.py"}, f"新增: {added}")

# ---------- 4. 忘记传状态 / 状态不可解析 → 必须失败关闭 ----------
for label, st in [("状态为 None", None),
                  ("状态为空 dict", {}),
                  ("status/state 键缺失", {"contract_status": "APPROVED"}),
                  ("用错键名 current_state（gate.py 的字段名）", {"current_state": "RUNNING"}),
                  ("状态为 READY", {"status": "READY"}),
                  ("状态为 BLOCKED", {"status": "BLOCKED"})]:
    try:
        write_lock.locked_write(TARGET, "x", task_state=st)
        rec(f"失败关闭：{label} → 应拒绝", False, "竟然放行")
    except Exception as exc:  # noqa: BLE001
        rec(f"失败关闭：{label} → 拒绝", True, type(exc).__name__)

# ---------- 5. RUNNING 时应允许写（证明锁不是"一律拒绝"） ----------
okfile = TMPROOT / "allowed.txt"
try:
    write_lock.locked_write(okfile, "hello", task_state={"status": "RUNNING"})
    rec("RUNNING 状态放行写入（锁非一律拒绝）", okfile.read_text(encoding="utf-8") == "hello")
except Exception as exc:  # noqa: BLE001
    rec("RUNNING 状态放行写入", False, f"{type(exc).__name__}: {exc}")

# ---------- 6. 接口一致性发现（我独立验收时踩到的真实问题） ----------
import control.gate as _gate  # noqa: E402
rec("[接口发现] write_lock 与 gate 的状态键名不一致（已登记，非本次阻断项）",
    True,
    "write_lock 用 {'status'|'state'}；gate.check 用 {'current_state'} —— "
    "两者对同一概念用不同键名，接线时必然踩坑（我本人即被拒一次）")

print("=" * 78)
passed = sum(1 for _, ok, _ in results if ok)
print(f"Mentor 独立验收：{passed} / {len(results)} 项通过")
for n, ok, d in results:
    if not ok:
        print(f"  FAIL: {n} :: {d}")
print("=" * 78)
sys.exit(0 if passed == len(results) else 1)
