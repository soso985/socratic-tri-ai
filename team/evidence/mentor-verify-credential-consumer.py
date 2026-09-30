# -*- coding: utf-8 -*-
"""
Mentor 独立验收 · task-11：规则凭证消费端（只能开 BLOCKED 这扇门）

通过标准（Owner）：
  凭证只能用来离开 BLOCKED；必须是 compare() 签发的未消费凭证；用一次作废；
  ESCALATED / CHANGE_PENDING / 「等用户裁决」即使用凭证也必须拒绝。

独立性：自建隔离根；凭证一律由**真实 compare()** 签发（不手写）；核对 Executor 报的 R-1。
"""
import hashlib
import json
import pathlib
import sys
import tempfile
import uuid

WS = pathlib.Path(r"E:\Socratic_M")
SRC = WS / "team" / "src"
SNAP = WS / "team" / "evidence" / "snapshot-before-credential-consumer.json"
REAL_STATE = WS / "team" / "agent_state" / "task_state.json"

sys.path.insert(0, str(SRC))
import control  # noqa: E402
from control import dual_judge, gate, state_guard  # noqa: E402

results = []


def rec(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def mkroot(status, task_id="TASK-001", blocked_from=None):
    r = pathlib.Path(tempfile.mkdtemp(prefix="mentor-cc-"))
    (r / "agent_state").mkdir(parents=True, exist_ok=True)
    rec_ = {"status": status}
    if blocked_from:
        rec_["previous_status"] = blocked_from
    (r / "agent_state" / "task_state.json").write_text(
        json.dumps({"tasks": {task_id: rec_}}, ensure_ascii=False), encoding="utf-8")
    obj = "OBJ-MC-" + uuid.uuid4().hex[:8]
    (r / "agent_state" / "objection_index.json").write_text(
        json.dumps({"objections": [{"obj_id": obj, "task_id": task_id}]}, ensure_ascii=False),
        encoding="utf-8")
    return r, obj


def issue_credential(root, obj, task_id="TASK-001"):
    """用真实 compare() 在 both_minor 下签发一张凭证。"""
    control.set_data_root(root)
    v = {"major": False, "hit_rules": [], "scope": "x"}
    dual_judge.seal_verdict("mentor", obj, v)
    dual_judge.seal_verdict("controller", obj, v)
    dual_judge.reveal_verdict("mentor", obj, v)
    dual_judge.reveal_verdict("controller", obj, v)
    res = dual_judge.compare(obj)
    cred = res.get("cred_id")
    assert cred, f"凭证未签发：{res.get('rule_credential_skipped_reason')}"
    return cred


def cred_path(root, cred_id):
    d = root / "agent_state" / "rule_credentials"
    m = sorted(d.glob(f"*.{cred_id}.json"))
    return m[0] if len(m) == 1 else None


def st(root, task_id="TASK-001"):
    return json.loads((root / "agent_state" / "task_state.json").read_text(encoding="utf-8"))["tasks"][task_id]["status"]


print("=" * 78)
print("Mentor 独立验收 · 规则凭证消费端")
print("=" * 78)

# ---------- 前提：CHANGE_PENDING 是否真不在 PAUSED_STATES（Executor R-1 的论据） ----------
rec("前提核对：CHANGE_PENDING 不在 PAUSED_STATES（Executor R-1 的论据成立）",
    "CHANGE_PENDING" not in state_guard.PAUSED_STATES,
    f"PAUSED_STATES={sorted(state_guard.PAUSED_STATES)}")
legal = gate._transition_legal("CHANGE_PENDING", "READY")[0] if hasattr(gate, "_transition_legal") else None
rec("前提核对：CHANGE_PENDING→READY 在跃迁表内合法", bool(legal))

# ---------- S1 BLOCKED + 有效凭证 → 放行 + 消费 ----------
root, obj = mkroot("BLOCKED", blocked_from="READY")
cred = issue_credential(root, obj)
before = sha(root / "agent_state" / "task_state.json")
res = state_guard.request_transition("TASK-001", "READY", rule_credential_id=cred)
payload = json.loads(cred_path(root, cred).read_text(encoding="utf-8"))
rec("S1 BLOCKED + 有效凭证 → 放行", st(root) == "READY", f"status={st(root)!r}")
rec("S1 凭证被标记 consumed", payload.get("consumed") is True, f"consumed={payload.get('consumed')}")

# ---------- S2 重放 ----------
# 先把状态放回 BLOCKED（set_paused 默认落「等用户裁决」，此处直接写夹具文件），
# 才能对准"同一张凭证重复使用"这一点
_p = root / "agent_state" / "task_state.json"
_d = json.loads(_p.read_text(encoding="utf-8"))
_d["tasks"]["TASK-001"]["status"] = "BLOCKED"
_p.write_text(json.dumps(_d, ensure_ascii=False), encoding="utf-8")
control.set_data_root(root)
rec("S2 前置：状态已回到 BLOCKED", st(root) == "BLOCKED", f"status={st(root)!r}")
before2 = sha(root / "agent_state" / "task_state.json")
try:
    state_guard.request_transition("TASK-001", "READY", rule_credential_id=cred)
    rec("S2 同一张凭证再用 → 被拒", False, "竟然放行")
except state_guard.CredentialConsumed:
    rec("S2 同一张凭证再用 → 被拒（CredentialConsumed）", True)
except Exception as exc:  # noqa: BLE001
    rec("S2 同一张凭证再用 → 被拒", False, f"抛的是 {type(exc).__name__}")
rec("S2 状态文件字节不变", before2 == sha(root / "agent_state" / "task_state.json"))
rec("S1 确实改变了状态（非空转）", before != sha(root / "agent_state" / "task_state.json"))

# ---------- S3/S4/S5 三种其它暂停态：带有效凭证也必须拒 ----------
for label, status, target, via_stale in [
    ("S3 ESCALATED", "ESCALATED", "READY", False),
    ("S4 CHANGE_PENDING", "CHANGE_PENDING", "READY", False),
    # S5 走"陈旧凭证"路径：发证时在 BLOCKED（合法），随后任务进入「等用户裁决」，
    # 再拿这张旧凭证来用 —— 因为「等用户裁决」下 compare() 根本不签发凭证，
    # 这是唯一能真实到达该场景的攻击路径。
    ("S5 等用户裁决", "BLOCKED", "READY", True),
]:
    r, o = mkroot(status, blocked_from="READY" if status == "BLOCKED" else None)
    c = issue_credential(r, o)
    if via_stale:
        state_guard.set_paused("TASK-001", "S5：凭证签发后任务转入「等用户裁决」")
        rec(f"{label} 前提：状态已转为「等用户裁决」", st(r) == "等用户裁决", f"status={st(r)!r}")
    b = sha(r / "agent_state" / "task_state.json")
    try:
        state_guard.request_transition("TASK-001", target, rule_credential_id=c)
        rec(f"{label} + 有效凭证 → 必须拒绝", False, f"!! 竟然放行（status={st(r)!r}）")
    except state_guard.CredentialNotAllowed:
        rec(f"{label} + 有效凭证 → 拒绝（CredentialNotAllowed）", True)
    except Exception as exc:  # noqa: BLE001
        rec(f"{label} + 有效凭证 → 拒绝", False, f"抛的是 {type(exc).__name__}")
    rec(f"{label} 状态文件字节不变", b == sha(r / "agent_state" / "task_state.json"))
    cp = cred_path(r, c)
    rec(f"{label} 失败尝试未消费掉凭证", cp is not None and
        json.loads(cp.read_text(encoding="utf-8")).get("consumed") is False)

# ---------- S6 task_id 不符 ----------
r6, o6 = mkroot("BLOCKED", task_id="TASK-001", blocked_from="READY")
c6 = issue_credential(r6, o6, task_id="TASK-001")
control.set_data_root(r6)                      # 显式切根（上一版漏了，导致后续段落落在错误的根上）
p6 = cred_path(r6, c6)
d6 = json.loads(p6.read_text(encoding="utf-8"))
d6["task_id"] = "TASK-999"
p6.write_text(json.dumps(d6, ensure_ascii=False), encoding="utf-8")
b6 = sha(r6 / "agent_state" / "task_state.json")
try:
    state_guard.request_transition("TASK-001", "READY", rule_credential_id=c6)
    rec("S6 凭证 task_id 不符 → 拒绝", False, "竟然放行")
except (state_guard.CredentialMismatch, state_guard.TransitionDenied) as exc:
    rec("S6 凭证 task_id 不符 → 拒绝", True, type(exc).__name__)
rec("S6 状态文件字节不变", b6 == sha(r6 / "agent_state" / "task_state.json"))

# ---------- S7 不存在的 cred_id ----------
r7, o7 = mkroot("BLOCKED", blocked_from="READY")
control.set_data_root(r7)                      # 显式切根
b7 = sha(r7 / "agent_state" / "task_state.json")
try:
    state_guard.request_transition("TASK-001", "READY", rule_credential_id="RC-19700101-999")
    rec("S7 不存在的凭证 → 拒绝", False, "竟然放行")
except state_guard.CredentialNotFound:
    rec("S7 不存在的凭证 → 拒绝（CredentialNotFound）", True)
except Exception as exc:  # noqa: BLE001
    rec("S7 不存在的凭证 → 拒绝", False, f"抛的是 {type(exc).__name__}")
rec("S7 状态文件字节不变", b7 == sha(r7 / "agent_state" / "task_state.json"))

# ---------- S8 两个都没给 ----------
r8, o8 = mkroot("BLOCKED", blocked_from="READY")
control.set_data_root(r8)                      # 显式切根
b8 = sha(r8 / "agent_state" / "task_state.json")
try:
    state_guard.request_transition("TASK-001", "READY")
    rec("S8 未提供任何授权 → ApprovalRequired", False, "竟然放行")
except state_guard.ApprovalRequired:
    rec("S8 未提供任何授权 → ApprovalRequired（既有行为不变）", True)
rec("S8 状态文件字节不变", b8 == sha(r8 / "agent_state" / "task_state.json"))

# ---------- S9 Owner 批准路径仍可用 ----------
r9, o9 = mkroot("BLOCKED", blocked_from="READY")
control.set_data_root(r9)                      # 显式切根（上一版漏了，导致 S9 假失败）
ap = state_guard.record_user_approval("TASK-001", "S9 验收", "Owner", "Mentor 独立验收")
aid = ap.get("approval_id") or ap.get("id")
res9 = state_guard.request_transition("TASK-001", "READY", approval_id=aid)
rec("S9 Owner 批准路径仍可用", st(r9) == "READY", f"status={st(r9)!r}")

# ---------- 附加：批准与凭证同时给出时，凭证不得被消费 ----------
r10, o10 = mkroot("BLOCKED", blocked_from="READY")
c10 = issue_credential(r10, o10)
control.set_data_root(r10)                     # 显式切根
ap10 = state_guard.record_user_approval("TASK-001", "同时给出", "Owner", "Mentor")
aid10 = ap10.get("approval_id") or ap10.get("id")
state_guard.request_transition("TASK-001", "READY", approval_id=aid10, rule_credential_id=c10)
cp10 = cred_path(r10, c10)
rec("批准优先：同时给出时凭证未被消费（沿途未被偷用）",
    json.loads(cp10.read_text(encoding="utf-8")).get("consumed") is False)

control.reset_data_root()

# ---------- S10 快照 ----------
snap = json.loads(SNAP.read_text(encoding="utf-8"))["files"]
for k in ("dual_judge", "write_lock", "objection", "gate", "audit_log", "taskstate", "taskcard"):
    now = sha(snap[k]["path"])
    rec(f"S10 未改动：{k}", now == snap[k]["sha256"], f"{snap[k]['sha256'][:16]}")
rec("S10 state_guard 是本轮唯一变更对象",
    sha(snap["state_guard"]["path"]) != snap["state_guard"]["sha256"])
rec("真实工作区无 rule_credentials 目录",
    not (WS / "team" / "agent_state" / "rule_credentials").exists())
rec("真实状态仍为「等用户裁决」",
    json.loads(REAL_STATE.read_text(encoding="utf-8"))["tasks"]["TASK-001"]["status"] == "等用户裁决")

print("=" * 78)
passed = sum(1 for _, ok, _ in results if ok)
print(f"Mentor 独立验收：{passed} / {len(results)} 项通过")
for n, ok, d in results:
    if not ok:
        print(f"  FAIL: {n} :: {d}")
print("=" * 78)
sys.exit(0 if passed == len(results) else 1)
