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

from control import ENV_DATA_ROOT, audit_log, gate, state_guard  # noqa: E402
from control.state_guard import (  # noqa: E402
    ApprovalConsumed,
    ApprovalMismatch,
    ApprovalNotFound,
    ApprovalRequired,
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
