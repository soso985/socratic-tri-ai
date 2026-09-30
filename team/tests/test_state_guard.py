"""task-9（Owner 直接指令）· 状态护栏 ``control.state_guard`` 的单元测试。

核心不变量（本文件用测试钉死）：
**程序自身没有任何路径，能在没有「用户已批准」记录时把任务从暂停类改成 RUNNING / 可执行。**

隔离：``CONTROL_DATA_ROOT`` 指向 ``tmp_path``，因此测试不会触碰真实
``team/agent_state/task_state.json`` 与真实批准记录。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from control import ENV_DATA_ROOT, audit_log, dual_judge, gate, objection, state_guard  # noqa: E402
from control.state_guard import (  # noqa: E402
    ApprovalConsumed,
    ApprovalMismatch,
    ApprovalNotFound,
    ApprovalRequired,
    CredentialConsumed,
    CredentialMismatch,
    CredentialNotAllowed,
    CredentialNotFound,
    IllegalTransition,
    InvalidApproval,
)

TASK = "TASK-001"
GATE_STATES = tuple(sorted(gate.ALL_STATES))


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_DATA_ROOT, str(tmp_path))
    return tmp_path


def _state_file(root: Path) -> Path:
    return root / "agent_state" / "task_state.json"


def _set_state(root: Path, status: str, task_id: str = TASK) -> Path:
    path = _state_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": "task-internal/v1", "tasks": {task_id: {"status": status}}},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _approval_file(root: Path, approval_id: str, task_id: str = TASK) -> Path:
    return root / "agent_state" / "approvals" / f"{task_id}.{approval_id}.json"


def _grant(task_id: str = TASK, scope: str = "解暂停", statement: str = "Owner 批准继续") -> str:
    return state_guard.record_user_approval(task_id, scope, "Owner", statement)["approval_id"]


# ================================================================ 暂停免费


def test_set_paused_needs_no_approval(isolated_data_root):
    _set_state(isolated_data_root, "RUNNING")
    result = state_guard.set_paused(TASK, "等待用户裁决")
    assert result["status"] == state_guard.DEFAULT_PAUSED_STATE == "等用户裁决"
    assert result["previous_status"] == "RUNNING"
    assert state_guard.current_status(TASK) == "等用户裁决"


def test_set_paused_is_allowed_from_any_state(isolated_data_root):
    for source in ("RUNNING", "READY", "SUBMITTED", "DRAFT", "BLOCKED"):
        _set_state(isolated_data_root, source)
        state_guard.set_paused(TASK, "停下来")
        assert state_guard.current_status(TASK) == "等用户裁决"


def test_set_paused_requires_reason(isolated_data_root):
    _set_state(isolated_data_root, "RUNNING")
    with pytest.raises(state_guard.StateGuardError):
        state_guard.set_paused(TASK, "   ")


# ================================================================ 批准记录


def test_record_user_approval_writes_exact_fields(isolated_data_root):
    result = state_guard.record_user_approval(TASK, "解暂停", "Owner", "可以继续")
    path = _approval_file(isolated_data_root, result["approval_id"])
    record = json.loads(path.read_text(encoding="utf-8"))
    assert set(record) == {"approval_id", "task_id", "approved_by", "approved_at",
                           "scope", "statement", "consumed"}
    assert record["approved_by"] == "Owner"
    assert record["consumed"] is False
    assert path.name == f"{TASK}.{result['approval_id']}.json"


def test_record_user_approval_ids_increment(isolated_data_root):
    first = state_guard.record_user_approval(TASK, "s", "Owner", "t")["approval_id"]
    second = state_guard.record_user_approval(TASK, "s", "Owner", "t")["approval_id"]
    assert first.endswith("001") and second.endswith("002")


@pytest.mark.parametrize("approver", ["Mentor", "mentor", "Controller", "Executor", "owner", "OWNER", "system", ""])
def test_forged_approver_is_rejected_and_nothing_is_written(isolated_data_root, approver):
    """S4 语义：批准人不是恰为 'Owner' → InvalidApproval **且不落盘**。"""
    approvals = isolated_data_root / "agent_state" / "approvals"
    with pytest.raises(InvalidApproval):
        state_guard.record_user_approval(TASK, "解暂停", approver, "我自己批准自己")
    assert not approvals.exists() or list(approvals.iterdir()) == []


def test_record_user_approval_rejects_empty_scope_or_statement(isolated_data_root):
    with pytest.raises(InvalidApproval):
        state_guard.record_user_approval(TASK, "  ", "Owner", "statement")
    with pytest.raises(InvalidApproval):
        state_guard.record_user_approval(TASK, "scope", "Owner", "   ")


def test_record_user_approval_does_not_accept_caller_supplied_id(isolated_data_root):
    with pytest.raises(TypeError):
        state_guard.record_user_approval(TASK, "s", "Owner", "t", approval_id="APR-FAKE")


# ================================================================ 核心不变量：无批准不得解暂停


@pytest.mark.parametrize("target", sorted(state_guard.EXECUTABLE_STATES))
def test_no_path_to_executable_without_approval(isolated_data_root, target):
    """核心不变量：暂停态 → 可执行态，无批准记录时必须拒绝且**一个字节都不写**。"""
    path = _set_state(isolated_data_root, state_guard.DEFAULT_PAUSED_STATE)
    before = _sha(path)
    with pytest.raises(ApprovalRequired):
        state_guard.request_transition(TASK, target)
    assert _sha(path) == before          # 连写都没写
    assert state_guard.current_status(TASK) == state_guard.DEFAULT_PAUSED_STATE


@pytest.mark.parametrize("paused", sorted(state_guard.PAUSED_STATES))
def test_every_paused_state_requires_approval_for_running(isolated_data_root, paused):
    path = _set_state(isolated_data_root, paused)
    before = _sha(path)
    with pytest.raises(ApprovalRequired):
        state_guard.request_transition(TASK, "RUNNING")
    assert _sha(path) == before


def test_leaving_pause_to_non_executable_also_requires_approval(isolated_data_root):
    """**堵多跳绕过**：BLOCKED → AUDITING（非可执行）同样要先解暂停。

    否则 ``BLOCKED → AUDITING → READY → RUNNING`` 三跳全部合法、
    且只有第一跳离开暂停，就能在**没有任何批准记录**的情况下到达 RUNNING。
    """
    path = _set_state(isolated_data_root, "BLOCKED")
    before = _sha(path)
    with pytest.raises(ApprovalRequired):
        state_guard.request_transition(TASK, "AUDITING")
    assert _sha(path) == before


def test_multi_hop_bypass_is_closed_end_to_end(isolated_data_root):
    """把整条绕过路径走一遍：每一步都应在第一跳就被拦下。"""
    _set_state(isolated_data_root, "BLOCKED")
    with pytest.raises(ApprovalRequired):
        state_guard.request_transition(TASK, "AUDITING")
    assert state_guard.current_status(TASK) == "BLOCKED"   # 第一跳就停住，后续无从谈起


def test_unknown_task_fails_closed(isolated_data_root):
    """读不到当前状态时按暂停处理：不能因为"读不到"就放行。"""
    path = _state_file(isolated_data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"schema": "task-internal/v1", "tasks": {}}', encoding="utf-8")
    before = _sha(path)
    with pytest.raises(ApprovalRequired):
        state_guard.request_transition("TASK-UNKNOWN", "RUNNING")
    assert _sha(path) == before


# ================================================================ 批准记录的各种无效情形


def test_missing_approval_record_is_rejected(isolated_data_root):
    path = _set_state(isolated_data_root, "BLOCKED")
    before = _sha(path)
    with pytest.raises(ApprovalNotFound):
        state_guard.request_transition(TASK, "RUNNING", approval_id="APR-20991231-999")
    assert _sha(path) == before


def test_approval_for_another_task_is_rejected(isolated_data_root):
    _set_state(isolated_data_root, "BLOCKED")
    other = state_guard.record_user_approval("TASK-OTHER", "解暂停", "Owner", "别的任务")
    path = _state_file(isolated_data_root)
    before = _sha(path)
    with pytest.raises(ApprovalNotFound):
        state_guard.request_transition(TASK, "RUNNING", approval_id=other["approval_id"])
    assert _sha(path) == before


def test_approval_with_non_owner_author_in_record_is_rejected(isolated_data_root):
    """即便有人绕过 record_user_approval 手写记录，非 Owner 也不认。"""
    _set_state(isolated_data_root, "BLOCKED")
    path = _approval_file(isolated_data_root, "APR-20260930-777")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"approval_id": "APR-20260930-777", "task_id": TASK,
                                "approved_by": "Mentor", "consumed": False}), encoding="utf-8")
    state_before = _sha(_state_file(isolated_data_root))
    with pytest.raises(ApprovalMismatch):
        state_guard.request_transition(TASK, "RUNNING", approval_id="APR-20260930-777")
    assert _sha(_state_file(isolated_data_root)) == state_before


def test_consumed_approval_cannot_be_replayed(isolated_data_root):
    """S6 语义：一次性凭证，重放必须被拒且不写盘。"""
    _set_state(isolated_data_root, "BLOCKED")
    approval_id = _grant()
    state_guard.request_transition(TASK, "RUNNING", approval_id=approval_id)
    assert state_guard.current_status(TASK) == "RUNNING"

    state_guard.set_paused(TASK, "再停一次")
    path = _state_file(isolated_data_root)
    before = _sha(path)
    with pytest.raises(ApprovalConsumed):
        state_guard.request_transition(TASK, "RUNNING", approval_id=approval_id)
    assert _sha(path) == before
    assert state_guard.current_status(TASK) == state_guard.DEFAULT_PAUSED_STATE


# ================================================================ 有批准则放行（不是一律拒绝）


def test_valid_approval_unlocks_paused_to_running(isolated_data_root):
    _set_state(isolated_data_root, "等用户裁决")
    approval_id = _grant(scope="解暂停继续执行", statement="Owner：批准恢复 TASK-001")
    result = state_guard.request_transition(TASK, "RUNNING", approval_id=approval_id)
    assert result["approval_required"] is True
    assert result["status"] == "RUNNING"
    assert state_guard.current_status(TASK) == "RUNNING"
    # 凭证被一次性消费
    assert state_guard.read_approval(TASK, approval_id)["consumed"] is True


def test_valid_approval_unlocks_paused_to_ready(isolated_data_root):
    _set_state(isolated_data_root, "ESCALATED")
    approval_id = _grant()
    state_guard.request_transition(TASK, "READY", approval_id=approval_id)
    assert state_guard.current_status(TASK) == "READY"


def test_approval_consumed_before_state_write(isolated_data_root):
    """消费先于状态写入（fail-closed）：凭证不会被重复使用。"""
    _set_state(isolated_data_root, "BLOCKED")
    approval_id = _grant()
    state_guard.request_transition(TASK, "RUNNING", approval_id=approval_id)
    record = state_guard.read_approval(TASK, approval_id)
    assert record["consumed"] is True and record["consumed_at"]


# ================================================================ 非暂停跃迁：无需批准，但受跃迁表约束


def test_normal_transition_needs_no_approval(isolated_data_root):
    _set_state(isolated_data_root, "RUNNING")
    result = state_guard.request_transition(TASK, "SUBMITTED")
    assert result["approval_required"] is False
    assert state_guard.current_status(TASK) == "SUBMITTED"


def test_ready_to_running_needs_no_approval(isolated_data_root):
    """正常运行不该被 Owner 打断（§12.3）。"""
    _set_state(isolated_data_root, "READY")
    state_guard.request_transition(TASK, "RUNNING")
    assert state_guard.current_status(TASK) == "RUNNING"


def test_illegal_non_paused_transition_is_rejected(isolated_data_root):
    path = _set_state(isolated_data_root, "SUBMITTED")
    before = _sha(path)
    with pytest.raises(IllegalTransition):
        state_guard.request_transition(TASK, "RUNNING")
    assert _sha(path) == before


def test_unknown_target_state_is_rejected(isolated_data_root):
    _set_state(isolated_data_root, "RUNNING")
    with pytest.raises(IllegalTransition):
        state_guard.request_transition(TASK, "NOT_A_STATE")


def test_guard_table_matches_gate_for_all_non_paused_pairs(isolated_data_root):
    """护栏的跃迁表校验与 ``gate`` 的 G36 判定**逐对一致**（不维护第二份表）。

    暂停类来源不走跃迁表（它们一律先要批准记录），故单独跳过——那部分由
    ``test_every_paused_state_requires_approval_for_running`` 等测试覆盖。
    """
    mismatches = []
    for source in GATE_STATES:
        if source in state_guard.PAUSED_STATES:
            continue
        for target in GATE_STATES:
            _set_state(isolated_data_root, source)
            gate_legal = "G36" not in gate.check({"current_state": source, "next_state": target})["hits"]
            try:
                state_guard.request_transition(TASK, target)
                guard_legal = True
            except IllegalTransition:
                guard_legal = False
            if guard_legal != gate_legal:
                mismatches.append((source, target, guard_legal, gate_legal))
    assert mismatches == []


# ================================================================ 审计留痕


def test_state_writes_are_audited(isolated_data_root):
    _set_state(isolated_data_root, "RUNNING")
    state_guard.set_paused(TASK, "等待裁决")
    approval_id = _grant()
    state_guard.request_transition(TASK, "RUNNING", approval_id=approval_id)
    log = isolated_data_root / "agent_state" / "audit_log.jsonl"
    events = [json.loads(line)["event"] for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    actions = [event["action"] for event in events]
    assert "status_written" in actions
    assert "user_approval_recorded" in actions
    assert all(event["kind"] == "state_guard" for event in events)


def test_denials_are_audited_without_touching_state(isolated_data_root):
    path = _set_state(isolated_data_root, "BLOCKED")
    before = _sha(path)
    with pytest.raises(ApprovalRequired):
        state_guard.request_transition(TASK, "RUNNING")
    assert _sha(path) == before
    log = isolated_data_root / "agent_state" / "audit_log.jsonl"
    events = [json.loads(line)["event"] for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    denial = [e for e in events if e["action"] == "transition_denied"]
    assert len(denial) == 1
    assert denial[0]["wrote_state"] is False


# ================================================================ 规则凭证消费端（task-11 · GUARD-01）


def _cred_dir(root: Path) -> Path:
    return root / "agent_state" / "rule_credentials"


def _issue_credential(root: Path, *, task_id: str = TASK) -> str:
    """用**真实的 compare()**（both_minor）签发一张规则凭证，返回 ``cred_id``。

    签发时任务须处于 ``BLOCKED``（签发端不允许「等用户裁决」）。
    """
    _set_state(root, "BLOCKED", task_id)
    obj_id = objection.submit(task_id, "v2", "premise", "消费端测试用异议", "见测试", "不主张失效")["obj_id"]
    _set_state(root, "BLOCKED", task_id)   # objection.submit 之后确保是 BLOCKED
    minor = {"major": False, "hit_rules": [], "scope": "不重大"}
    dual_judge.seal_verdict("mentor", obj_id, minor)
    dual_judge.seal_verdict("controller", obj_id, minor)
    dual_judge.reveal_verdict("mentor", obj_id, minor)
    dual_judge.reveal_verdict("controller", obj_id, minor)
    result = dual_judge.compare(obj_id)
    assert result["rule_credential"] is not None, result["rule_credential_skipped_reason"]
    return result["cred_id"]


def _cred_file(root: Path, cred_id: str) -> Path:
    matches = sorted(_cred_dir(root).glob(f"*.{cred_id}.json"))
    assert len(matches) == 1, matches
    return matches[0]


def test_credential_unlocks_blocked_and_is_consumed(isolated_data_root):
    """S1：BLOCKED + 有效未消费凭证 → 放行，状态改变，凭证被标记 consumed:true。"""
    cred_id = _issue_credential(isolated_data_root)
    assert json.loads(_cred_file(isolated_data_root, cred_id).read_text(encoding="utf-8"))["consumed"] is False

    result = state_guard.request_transition(TASK, "RUNNING", rule_credential_id=cred_id)
    assert result["status"] == "RUNNING"
    assert result["rule_credential_consumed"] is True
    assert state_guard.current_status(TASK) == "RUNNING"
    record = json.loads(_cred_file(isolated_data_root, cred_id).read_text(encoding="utf-8"))
    assert record["consumed"] is True and record["consumed_at"]


def test_credential_replay_is_rejected(isolated_data_root):
    """S2：同一张凭证用第二次 → 拒绝且状态文件不变。"""
    cred_id = _issue_credential(isolated_data_root)
    state_guard.request_transition(TASK, "RUNNING", rule_credential_id=cred_id)
    _set_state(isolated_data_root, "BLOCKED")
    path = _state_file(isolated_data_root)
    before = _sha(path)
    with pytest.raises(CredentialConsumed):
        state_guard.request_transition(TASK, "RUNNING", rule_credential_id=cred_id)
    assert _sha(path) == before
    assert state_guard.current_status(TASK) == "BLOCKED"


@pytest.mark.parametrize("paused_state", ["ESCALATED", "CHANGE_PENDING", "等用户裁决"])
def test_credential_rejected_for_every_other_paused_state(isolated_data_root, paused_state):
    """S3/S4/S5：规则凭证**只能开 BLOCKED 这一扇门**——其它暂停态即使带有效凭证也拒绝。"""
    cred_id = _issue_credential(isolated_data_root)          # 在 BLOCKED 下签发（有效、未消费）
    _set_state(isolated_data_root, paused_state)             # 再切到其它暂停态
    path = _state_file(isolated_data_root)
    before = _sha(path)
    with pytest.raises(CredentialNotAllowed):
        state_guard.request_transition(TASK, "RUNNING", rule_credential_id=cred_id)
    assert _sha(path) == before
    assert state_guard.current_status(TASK) == paused_state
    # 凭证**不得**被这次失败的尝试消费掉
    assert json.loads(_cred_file(isolated_data_root, cred_id).read_text(encoding="utf-8"))["consumed"] is False


def test_credential_with_mismatched_task_id_is_rejected(isolated_data_root):
    """S6：凭证的 task_id 与目标任务不一致 → 拒绝。"""
    cred_id = _issue_credential(isolated_data_root, task_id="TASK-OTHER")
    _set_state(isolated_data_root, "BLOCKED")                # 本次目标是 TASK-001
    path = _state_file(isolated_data_root)
    before = _sha(path)
    with pytest.raises(CredentialMismatch):
        state_guard.request_transition(TASK, "RUNNING", rule_credential_id=cred_id)
    assert _sha(path) == before
    assert json.loads(_cred_file(isolated_data_root, cred_id).read_text(encoding="utf-8"))["consumed"] is False


def test_unknown_credential_id_is_rejected(isolated_data_root):
    """S7：cred_id 不存在 → 拒绝（0 个匹配，fail-closed）。"""
    path = _set_state(isolated_data_root, "BLOCKED")
    before = _sha(path)
    with pytest.raises(CredentialNotFound):
        state_guard.request_transition(TASK, "RUNNING", rule_credential_id="RC-20991231-999")
    assert _sha(path) == before


def test_ambiguous_credential_match_is_rejected(isolated_data_root):
    """② 唯一匹配：同名 cred_id 出现两份 → 拒绝（不猜）。"""
    cred_id = _issue_credential(isolated_data_root)
    source = _cred_file(isolated_data_root, cred_id)
    (source.parent / f"ANOTHER-OBJ.{cred_id}.json").write_bytes(source.read_bytes())
    path = _state_file(isolated_data_root)
    before = _sha(path)
    with pytest.raises(CredentialNotFound):
        state_guard.request_transition(TASK, "RUNNING", rule_credential_id=cred_id)
    assert _sha(path) == before


@pytest.mark.parametrize("bad_id", ["", "   ", "RC-*", "../x", "RC-20260930-001/../x"])
def test_malformed_credential_id_is_rejected(isolated_data_root, bad_id):
    path = _set_state(isolated_data_root, "BLOCKED")
    before = _sha(path)
    with pytest.raises(CredentialNotFound):
        state_guard.request_transition(TASK, "RUNNING", rule_credential_id=bad_id)
    assert _sha(path) == before


def test_none_credential_means_not_provided(isolated_data_root):
    """``rule_credential_id=None`` 等同于"没给凭证" → 仍是既有行为 ApprovalRequired（S8）。"""
    path = _set_state(isolated_data_root, "BLOCKED")
    before = _sha(path)
    with pytest.raises(ApprovalRequired):
        state_guard.request_transition(TASK, "RUNNING", rule_credential_id=None)
    assert _sha(path) == before


def test_credential_not_issued_by_compare_is_rejected(isolated_data_root):
    """③ 必须是 compare() 签发的：issued_by 被改动 → 拒绝。"""
    cred_id = _issue_credential(isolated_data_root)
    path = _cred_file(isolated_data_root, cred_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["issued_by"] = "someone.else"
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CredentialMismatch):
        state_guard.request_transition(TASK, "RUNNING", rule_credential_id=cred_id)


def test_credential_with_wrong_basis_is_rejected(isolated_data_root):
    """③ basis/outcome 必须表明 §8.5 both_minor。"""
    cred_id = _issue_credential(isolated_data_root)
    path = _cred_file(isolated_data_root, cred_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["basis"] = "§8.5 both_major"
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CredentialMismatch):
        state_guard.request_transition(TASK, "RUNNING", rule_credential_id=cred_id)


def test_no_authorization_still_raises_approval_required(isolated_data_root):
    """S8：两个都没给 → ApprovalRequired（既有行为不变）。"""
    path = _set_state(isolated_data_root, "BLOCKED")
    before = _sha(path)
    with pytest.raises(ApprovalRequired):
        state_guard.request_transition(TASK, "RUNNING")
    assert _sha(path) == before


def test_owner_approval_path_still_works(isolated_data_root):
    """S9：Owner 批准路径仍可用（既有行为不变）。"""
    _set_state(isolated_data_root, "BLOCKED")
    approval_id = _grant()
    result = state_guard.request_transition(TASK, "RUNNING", approval_id=approval_id)
    assert result["status"] == "RUNNING" and result["approval_required"] is True
    assert state_guard.read_approval(TASK, approval_id)["consumed"] is True


def test_approval_takes_precedence_when_both_provided(isolated_data_root):
    """两者同时给出时以 approval_id 为准（既有路径优先），凭证不被消费。"""
    cred_id = _issue_credential(isolated_data_root)
    approval_id = _grant()
    result = state_guard.request_transition(TASK, "RUNNING", approval_id=approval_id,
                                            rule_credential_id=cred_id)
    assert result["approval_required"] is True
    assert result["rule_credential_consumed"] is False
    assert json.loads(_cred_file(isolated_data_root, cred_id).read_text(encoding="utf-8"))["consumed"] is False


def test_credential_denials_are_audited_without_touching_state(isolated_data_root):
    path = _set_state(isolated_data_root, "ESCALATED")
    before = _sha(path)
    cred_id = _issue_credential(isolated_data_root)   # 先在 BLOCKED 下签发，再切回 ESCALATED
    _set_state(isolated_data_root, "ESCALATED")
    with pytest.raises(CredentialNotAllowed):
        state_guard.request_transition(TASK, "RUNNING", rule_credential_id=cred_id)
    assert _sha(path) == before
    log = isolated_data_root / "agent_state" / "audit_log.jsonl"
    events = [json.loads(line)["event"] for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    denial = [e for e in events if e.get("action") == "transition_denied"
              and e.get("rule_credential_id") == cred_id]
    assert denial and denial[-1]["wrote_state"] is False
