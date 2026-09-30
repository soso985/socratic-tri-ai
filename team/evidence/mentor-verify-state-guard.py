# -*- coding: utf-8 -*-
"""
Mentor 独立验收 · task-9：状态护栏（封 RES-01 实质）

通过标准（Owner）：
  状态停在「等用户裁决」；无用户批准记录时写交付物必须被拒、文件一字节不变；
  不允许程序自己把状态从暂停改回 RUNNING 或可执行；谁改状态都必须留下「用户已批准」的记录。

独立性：自建隔离数据根；并**自行复现 Executor 报告的多跳绕过路径**。
"""
import hashlib
import json
import pathlib
import sys
import tempfile
import uuid

WS = pathlib.Path(r"E:\Socratic_M")
SRC = WS / "team" / "src"
SNAP = WS / "team" / "evidence" / "snapshot-before-state-guard.json"
TASKCARD = WS / "team" / "milestones" / "M1" / "TASK-001-v2.md"
REAL_STATE = WS / "team" / "agent_state" / "task_state.json"

sys.path.insert(0, str(SRC))
import control  # noqa: E402
from control import gate, state_guard, write_lock  # noqa: E402

results = []


def rec(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def new_root(status, task_id="TASK-001"):
    r = pathlib.Path(tempfile.mkdtemp(prefix="mentor-guard-"))
    (r / "agent_state" / "approvals").mkdir(parents=True, exist_ok=True)
    (r / "agent_state" / "task_state.json").write_text(
        json.dumps({"tasks": {task_id: {"status": status}}}, ensure_ascii=False), encoding="utf-8")
    return r


def st(root, task_id="TASK-001"):
    return json.loads((root / "agent_state" / "task_state.json").read_text(encoding="utf-8"))["tasks"][task_id]["status"]


print("=" * 78)
print("Mentor 独立验收 · 状态护栏")
print("=" * 78)

# ============ A. 真实工作区：状态与交付物 ============
control.reset_data_root()
real_status = st(WS / "team")
rec("S1 真实状态已停在「等用户裁决」", real_status == "等用户裁决", f"status={real_status!r}")

card_before, card_stat = sha(TASKCARD), TASKCARD.stat()
raised = None
try:
    write_lock.locked_write(TASKCARD, "TAMPER\n", task_id="TASK-001")
except Exception as exc:  # noqa: BLE001
    raised = exc
rec("S2 无批准记录写交付物 → 被拒（WriteDenied）",
    isinstance(raised, write_lock.WriteDenied), type(raised).__name__ if raised else "NO RAISE")
rec("S2 ★ 交付物一个字节不变", card_before == sha(TASKCARD),
    f"{card_before[:16].upper()} -> {sha(TASKCARD)[:16].upper()}")
rec("S2 交付物字节数与 mtime 未变",
    card_stat.st_size == TASKCARD.stat().st_size and card_stat.st_mtime_ns == TASKCARD.stat().st_mtime_ns)

# ============ B. 隔离根：核心不变量 ============
root = new_root("等用户裁决")
control.set_data_root(root)

before = sha(root / "agent_state" / "task_state.json")
try:
    state_guard.request_transition("TASK-001", "RUNNING")
    rec("S3 程序自行解锁（无凭证）→ 被拒", False, "竟然成功")
except state_guard.ApprovalRequired as exc:
    rec("S3 程序自行解锁（无凭证）→ ApprovalRequired", True, str(exc)[:60])
except Exception as exc:  # noqa: BLE001
    rec("S3 程序自行解锁 → 被拒", False, f"抛的是 {type(exc).__name__}")
rec("S3 ★ 状态文件哈希前后完全相同（连写都没写）", before == sha(root / "agent_state" / "task_state.json"),
    f"{before[:16].upper()} -> {sha(root / 'agent_state' / 'task_state.json')[:16].upper()}")

# ---------- 多跳绕过：Executor 报的 R-2，我自己复现 ----------
print("\n--- 多跳绕过复现（Executor R-2）---")
print("    §10.1 下 BLOCKED→AUDITING→READY→RUNNING 三跳是否都合法：")
for s, t in [("BLOCKED", "AUDITING"), ("AUDITING", "READY"), ("READY", "RUNNING")]:
    legal = gate._transition_legal(s, t)[0] if hasattr(gate, "_transition_legal") else None
    print(f"      {s} -> {t} : {'合法' if legal else '非法'}")
print("    → 三跳全合法即构成绕过路径（这正是 Executor 报的 R-2）")

root2 = new_root("BLOCKED")
control.set_data_root(root2)
bypass_blocked_at = None
try:
    state_guard.request_transition("TASK-001", "AUDITING")   # 第一跳：离开暂停、落点非可执行
    rec("多跳第一跳 BLOCKED→AUDITING 即被要求凭证（绕过已封）", False, "竟然放行——绕过仍存在")
    bypass_blocked_at = None
except state_guard.ApprovalRequired:
    bypass_blocked_at = "AUDITING"
    rec("多跳第一跳 BLOCKED→AUDITING 即被要求凭证（绕过已封）", True, "ApprovalRequired")

# 逐个暂停态 × 逐个可执行态，全部必须要求凭证
for ps in sorted(state_guard.PAUSED_STATES):
    r2 = new_root(ps)
    control.set_data_root(r2)
    ok_all = True
    for es in sorted(state_guard.EXECUTABLE_STATES):
        try:
            state_guard.request_transition("TASK-001", es)
            ok_all = False
        except state_guard.ApprovalRequired:
            pass
    rec(f"暂停态 {ps!r} → 任一可执行态均须凭证", ok_all)

# ============ C. 批准记录 ============
root3 = new_root("等用户裁决")
control.set_data_root(root3)
apdir = root3 / "agent_state" / "approvals"
before_files = sorted(p.name for p in apdir.glob("*"))
try:
    state_guard.record_user_approval("TASK-001", "s", "Mentor", "冒充")
    rec("S4 伪造批准人（Mentor）→ InvalidApproval", False, "竟然接受")
except state_guard.InvalidApproval:
    rec("S4 伪造批准人（Mentor）→ InvalidApproval", True)
rec("S4 未落盘", sorted(p.name for p in apdir.glob("*")) == before_files,
    f"{sorted(p.name for p in apdir.glob('*'))}")

ap = state_guard.record_user_approval("TASK-001", "task-9 验收用", "Owner", "Mentor 独立验收构造")
aid = ap.get("approval_id") or ap.get("id")
rec("S5 记录 Owner 批准 → 落盘", (apdir / f"TASK-001.{aid}.json").exists(), f"approval_id={aid}")

r = state_guard.request_transition("TASK-001", "RUNNING", approval_id=aid)
rec("S5 携带凭证 → 放行且状态确变为 RUNNING", st(root3) == "RUNNING", f"status={st(root3)!r}")
rec("S5 凭证被标记为已消费",
    json.loads((apdir / f"TASK-001.{aid}.json").read_text(encoding="utf-8")).get("consumed") is True)

state_guard.set_paused("TASK-001", "S6 前置")
before6 = sha(root3 / "agent_state" / "task_state.json")
try:
    state_guard.request_transition("TASK-001", "RUNNING", approval_id=aid)
    rec("S6 同一凭证重放 → 被拒", False, "竟然放行")
except (state_guard.ApprovalConsumed, state_guard.TransitionDenied) as exc:
    rec("S6 同一凭证重放 → 被拒", True, type(exc).__name__)
rec("S6 状态文件字节不变", before6 == sha(root3 / "agent_state" / "task_state.json"))

control.reset_data_root()

# ============ D. 快照：既有模块必须原样 ============
snap = json.loads(SNAP.read_text(encoding="utf-8"))["files"]
for k, v in snap.items():
    now = sha(v["path"])
    if k == "taskstate":
        rec("taskstate 是本轮唯一预期改写项", now != v["sha256"], f"{v['sha256'][:16]} -> {now[:16]}")
    else:
        rec(f"未改动：{k}", now == v["sha256"], f"{v['sha256'][:16]}")

# ============ E. 收尾：真实状态仍是暂停类 ============
control.reset_data_root()
rec("真实 TASK-001 结束时仍在暂停类（未被留在可执行态）",
    st(WS / "team") in state_guard.PAUSED_STATES, f"status={st(WS / 'team')!r}")

print("=" * 78)
passed = sum(1 for _, ok, _ in results if ok)
print(f"Mentor 独立验收：{passed} / {len(results)} 项通过")
for n, ok, d in results:
    if not ok:
        print(f"  FAIL: {n} :: {d}")
print("=" * 78)
sys.exit(0 if passed == len(results) else 1)
