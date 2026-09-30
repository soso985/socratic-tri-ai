# -*- coding: utf-8 -*-
"""
Mentor 独立验收 · task-7：异议 → 暂停 → 任务卡写入被拒

通过标准（Owner）：
  异议一提交，任务状态必须变为暂停；之后对任务卡的写入必须被拒绝；
  任务卡改前改后哈希相同，一个字节不变。

独立性：本脚本自行构造因果对照，不复用 Executor 的演示代码。
"""
import hashlib
import json
import pathlib
import shutil
import sys
import tempfile

SRC = pathlib.Path(r"E:\Socratic_M\team\src")
SNAP = pathlib.Path(r"E:\Socratic_M\team\evidence\snapshot-before-objection-lockdown.json")
TASKCARD = pathlib.Path(r"E:\Socratic_M\team\milestones\M1\TASK-001-v2.md")

sys.path.insert(0, str(SRC))
import control  # noqa: E402
from control import objection, write_lock  # noqa: E402

results = []


def rec(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def make_root(status, task_id="TASK-001"):
    root = pathlib.Path(tempfile.mkdtemp(prefix="mentor-ld-"))
    d = root / "agent_state"
    d.mkdir(parents=True, exist_ok=True)
    (d / "task_state.json").write_text(
        json.dumps({"schema": "task-internal/v1",
                    "tasks": {task_id: {"status": status, "previous_status": None,
                                        "contract_version": "v2"}}}, ensure_ascii=False),
        encoding="utf-8")
    for sub in ("proposals",):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def read_status(root, task_id="TASK-001"):
    p = root / "agent_state" / "task_state.json"
    return json.loads(p.read_text(encoding="utf-8"))["tasks"][task_id]["status"]


print("=" * 78)
print("Mentor 独立验收 · 异议即暂停 → 任务卡写入被拒")
print("=" * 78)

# ============ A. 因果链（隔离根，自行构造） ============
root = make_root("RUNNING")
cardcopy = root / "TASK-001-v2.md"
shutil.copyfile(TASKCARD, cardcopy)
before_copy = sha(cardcopy)

control.set_data_root(root)
rec("前提：隔离根权威状态 = RUNNING", read_status(root) == "RUNNING")

# A1: RUNNING 时写入应放行
try:
    write_lock.locked_write(cardcopy, "MODIFIED BEFORE OBJECTION\n", task_id="TASK-001")
    rec("A1 异议前（RUNNING）写入放行", sha(cardcopy) != before_copy,
        f"副本哈希已变 {before_copy[:16]} -> {sha(cardcopy)[:16]}")
except Exception as exc:  # noqa: BLE001
    rec("A1 异议前（RUNNING）写入放行", False, f"{type(exc).__name__}: {exc}")

# A2: 提交异议 → 状态必须变暂停
st_before = read_status(root)
r = objection.submit(task_id="TASK-001", version="v2", kind="premise",
                     claim="Mentor 独立验收构造的因果对照异议",
                     evidence="由 evidence/mentor-verify-lockdown.py 注入，仅用于验证链路",
                     invalidates="unknown")
obj_id = r["obj_id"]
st_after = read_status(root)
rec("A2 异议提交 → 任务状态变为暂停",
    st_before == "RUNNING" and st_after == "BLOCKED" and st_after != st_before,
    f"{st_before} -> {st_after}（obj_id={obj_id}）")

# A3: 异议之后，对同一路径写入必须被拒
card_at_obj = sha(cardcopy)
try:
    write_lock.locked_write(cardcopy, "MODIFIED AFTER OBJECTION\n", task_id="TASK-001")
    rec("A3 异议后（BLOCKED）写入被拒", False, "!! 竟然放行")
except write_lock.WriteDenied as exc:
    rec("A3 异议后（BLOCKED）写入被拒", True, f"WriteDenied: {str(exc)[:70]}")
    rec("A3b 拒绝后副本仍未变", sha(cardcopy) == card_at_obj,
        f"{card_at_obj[:16]} == {sha(cardcopy)[:16]}")
except Exception as exc:  # noqa: BLE001
    rec("A3 异议后（BLOCKED）写入被拒", False, f"抛的是 {type(exc).__name__}")

# A4: 拒绝是否留下审计记录
log = root / "agent_state" / "audit_log.jsonl"
lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
denials = [x for x in lines if x.get("event", {}).get("kind") == "write_lock.denied"]
rec("A4 拒绝已记入审计日志", len(denials) >= 1, f"拒绝记录 {len(denials)} 条")
if denials:
    ev = denials[-1]["event"]
    rec("A4b 拒绝记录写明权威状态为暂停态", ev.get("state") == "BLOCKED", f"state={ev.get('state')!r}")

# A5: 反向对照 —— 未提异议时同一路径可写（证明 A3 不是"路径被永久锁死"）
root2 = make_root("RUNNING")
card2 = root2 / "TASK-001-v2.md"
shutil.copyfile(TASKCARD, card2)
control.set_data_root(root2)
try:
    write_lock.locked_write(card2, "x\n", task_id="TASK-001")
    rec("A5 反向对照：另一隔离根（RUNNING、未提异议）同一路径仍可写", True)
except Exception as exc:  # noqa: BLE001
    rec("A5 反向对照：仍可写", False, f"{type(exc).__name__}")

# ============ B. 真实工作区：对真实任务卡的尝试 ============
control.reset_data_root()
card_before = sha(TASKCARD)
st_real = json.loads((pathlib.Path(r"E:\Socratic_M\team\agent_state\task_state.json"))
                     .read_text(encoding="utf-8"))["tasks"]["TASK-001"]["status"]
stat_before = TASKCARD.stat()
raised = None
try:
    write_lock.locked_write(TASKCARD, "TAMPERED BY MENTOR\n", task_id="TASK-001")
except Exception as exc:  # noqa: BLE001
    raised = exc
stat_after = TASKCARD.stat()
rec("B1 真实工作区：对真实任务卡的写入被拒",
    isinstance(raised, write_lock.WriteDenied), f"{type(raised).__name__ if raised else 'NO RAISE'}")
rec("B2 ★ 通过标准：任务卡内容一个字节都不变", card_before == sha(TASKCARD),
    f"{card_before[:16].upper()} -> {sha(TASKCARD)[:16].upper()}")
rec("B3 任务卡字节数与 mtime 未变",
    stat_before.st_size == stat_after.st_size and stat_before.st_mtime_ns == stat_after.st_mtime_ns,
    f"{stat_before.st_size} B")
rec("B4 真实权威状态确为暂停态（前提核对）", st_real == "BLOCKED", f"status={st_real}")

# ============ C. 快照：锁、任务卡、gate、objection 必须未变 ============
snap = json.loads(SNAP.read_text(encoding="utf-8"))["files"]
for key in ("lock", "taskcard", "gate", "objection"):
    p = pathlib.Path(snap[key]["path"])
    now = sha(p)
    rec(f"C 未改动：{key}（{p.name}）", now == snap[key]["sha256"],
        f"{snap[key]['sha256'][:16]} -> {now[:16]}")

print("=" * 78)
passed = sum(1 for _, ok, _ in results if ok)
print(f"Mentor 独立验收：{passed} / {len(results)} 项通过")
for n, ok, d in results:
    if not ok:
        print(f"  FAIL: {n} :: {d}")
print("=" * 78)
sys.exit(0 if passed == len(results) else 1)
