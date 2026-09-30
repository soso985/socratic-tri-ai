"""TASK-001 v2 · must_do #5 —— 确定性控制程序单元测试。

契约：``team/milestones/M1/TASK-001-v2.md``（唯一有效版本）
唯一测试命令（见契约「验收环境」表）::

    <指定解释器> -m pytest "E:\\Socratic_M\\team\\tests" -v

覆盖面：``gate`` / ``objection`` / ``dual_judge`` / ``audit_log`` 四个模块各 ≥3 条；
并逐条覆盖契约 must_do#5 列出的**必测项**（G37 双向、异议 ID 不可外部指定、
``READY→BLOCKED`` 合法与 ``SUBMITTED→RUNNING`` 非法、单方提交抛
``IncompleteJudgment``、三种 outcome、审计日志抗篡改）。

隔离说明：本测试通过 ``CONTROL_DATA_ROOT`` 把程序的数据根重定向到 pytest 的
``tmp_path``，因此运行测试**不会**向 ``team/agent_state/`` 或 ``team/proposals/``
写入任何文件（那些路径属 Executor 的 ``forbidden_scope``）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------- 导入路径
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from control import ENV_DATA_ROOT, audit_log, dual_judge, gate, objection  # noqa: E402
from control.dual_judge import (  # noqa: E402
    AlreadySealed,
    CommitmentMismatch,
    IncompleteJudgment,
    SealedVerdict,
)


# ---------------------------------------------------------------- 夹具


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path, monkeypatch):
    """把程序数据根指向临时目录，保证测试不写工作区（含 forbidden_scope 路径）。"""
    monkeypatch.setenv(ENV_DATA_ROOT, str(tmp_path))
    return tmp_path


def _valid_task(**overrides) -> dict:
    """一份「合法通过」的 task 字典（用于对照，不含任何门禁命中）。"""
    task = {
        "task_id": "TASK-001",
        "contract_status": "APPROVED",
        "contract_version": "v2",
        "task_version": "v2",
        "dependencies_met": True,
        "write_paths": [
            "team/src/control/gate.py",
            "team/tests/test_control.py",
            "team/evidence/TASK-001-run.log",
        ],
        "current_state": "SUBMITTED",
        "next_state": "ACCEPTED",
        "report": {
            "changes": "新增四个模块",
            "test_evidence": "pytest 全绿",
            "risks": "无",
            "deviations": "无",
            "rollback": "删除文件即回退",
        },
        "change_pending": False,
        "affected_by_change": False,
        "budget_exceeded": False,
        "rework_count": 0,
        "rework_limit": 2,
        "risk_over_threshold": False,
        "awaiting_user_decision": False,
        "is_paused": False,
    }
    task.update(overrides)
    return task


def _write_task_state(root: Path, task_id: str, status: str) -> None:
    """模拟「该任务此前已处于某状态」，用于验证 BLOCKED 的进入前状态记录。"""
    path = root / "agent_state" / "task_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": "task-internal/v1", "tasks": {task_id: {"status": status}}},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ================================================================ gate


def test_gate_valid_task_passes_with_no_hits():
    result = gate.check(_valid_task())
    assert result["hits"] == []
    assert result["blocked"] is False
    assert result["reasons"] == []


def test_g31_blocks_when_contract_not_approved():
    result = gate.check(_valid_task(contract_status="DRAFT"))
    assert "G31" in result["hits"]
    assert result["blocked"] is True
    assert any("G31" in reason for reason in result["reasons"])


def test_g32_blocks_on_version_mismatch():
    result = gate.check(_valid_task(task_version="v1"))
    assert "G32" in result["hits"]
    assert result["blocked"] is True


def test_g32_blocks_on_write_path_outside_allowed_paths():
    result = gate.check(_valid_task(write_paths=["team/charter/REQ-records.md"]))
    assert "G32" in result["hits"]
    assert any("allowed_paths" in reason for reason in result["reasons"])


def test_g32_blocks_on_path_traversal_escape():
    """``team/src/../milestones/...`` 归一化后落在白名单外，必须被拒。"""
    result = gate.check(_valid_task(write_paths=["team/src/../milestones/M1/TASK-001-v2.md"]))
    assert "G32" in result["hits"]


def test_g32_accepts_absolute_path_inside_workspace():
    result = gate.check(_valid_task(write_paths=[r"E:\Socratic_M\team\src\control\gate.py"]))
    assert "G32" not in result["hits"]


def test_g32_blocks_when_dependencies_not_met():
    result = gate.check(_valid_task(dependencies_met=False))
    assert "G32" in result["hits"]


@pytest.mark.parametrize(
    "item",
    ["changes", "test_evidence", "risks", "deviations", "rollback"],
)
def test_g33_blocks_when_report_item_missing(item):
    report = _valid_task()["report"]
    report.pop(item)
    result = gate.check(_valid_task(report=report))
    assert "G33" in result["hits"]
    assert result["blocked"] is True


def test_g33_blocks_empty_report_item():
    report = _valid_task()["report"]
    report["rollback"] = "   "
    result = gate.check(_valid_task(report=report))
    assert "G33" in result["hits"]


def test_g33_accepts_chinese_report_keys():
    result = gate.check(
        _valid_task(
            report={
                "真实变更": "x",
                "测试证据": "y",
                "风险": "无",
                "偏差": "无",
                "回滚点": "删除文件",
            }
        )
    )
    assert "G33" not in result["hits"]


def test_g33_blocks_acceptance_without_report():
    task = _valid_task(current_state="SUBMITTED", next_state="ACCEPTED")
    task.pop("report")
    result = gate.check(task)
    assert "G33" in result["hits"]


def test_g34_blocks_when_change_pending_affects_task():
    result = gate.check(_valid_task(change_pending=True, affected_by_change=True))
    assert "G34" in result["hits"]
    assert result["blocked"] is True


def test_g34_not_hit_when_change_does_not_affect_task():
    result = gate.check(_valid_task(change_pending=True, affected_by_change=False))
    assert "G34" not in result["hits"]


def test_g35_triggers_on_budget_exceeded():
    result = gate.check(_valid_task(budget_exceeded=True))
    assert "G35" in result["hits"]


def test_g35_triggers_on_rework_over_limit():
    result = gate.check(_valid_task(rework_count=3, rework_limit=2))
    assert "G35" in result["hits"]


def test_g35_not_hit_at_rework_equal_to_limit():
    """契约口径为 ``rework_count > rework_limit``（严格大于）。"""
    result = gate.check(_valid_task(rework_count=2, rework_limit=2))
    assert "G35" not in result["hits"]


def test_g35_triggers_on_risk_over_threshold():
    result = gate.check(_valid_task(risk_over_threshold=True))
    assert "G35" in result["hits"]


def test_g36_blocks_illegal_transition_submitted_to_running():
    result = gate.check(_valid_task(current_state="SUBMITTED", next_state="RUNNING"))
    assert "G36" in result["hits"]
    assert result["blocked"] is True


def test_g36_accepts_legal_transition_draft_to_auditing():
    result = gate.check(_valid_task(current_state="DRAFT", next_state="AUDITING"))
    assert "G36" not in result["hits"]


def test_g36_accepts_v2_added_transition_ready_to_blocked():
    """验收项 8：``READY → BLOCKED`` 合法（v2 依 V1.4-ERR-01 追加）。"""
    result = gate.check(_valid_task(current_state="READY", next_state="BLOCKED"))
    assert "G36" not in result["hits"], result["reasons"]


def test_g36_accepts_v2_added_transition_auditing_to_blocked():
    result = gate.check(_valid_task(current_state="AUDITING", next_state="BLOCKED"))
    assert "G36" not in result["hits"]


def test_g36_accepts_blocked_returning_to_previous_state():
    result = gate.check(_valid_task(current_state="BLOCKED", next_state="READY", blocked_from="READY"))
    assert "G36" not in result["hits"]


def test_g36_rejects_blocked_to_state_it_could_not_come_from():
    """``BLOCKED`` 不能直接进入 ``ACCEPTED``——ACCEPTED 不是可进入 BLOCKED 的状态。"""
    result = gate.check(_valid_task(current_state="BLOCKED", next_state="ACCEPTED"))
    assert "G36" in result["hits"]


def test_g36_rejects_blocked_to_state_conflicting_with_blocked_from():
    result = gate.check(_valid_task(current_state="BLOCKED", next_state="READY", blocked_from="RUNNING"))
    assert "G36" in result["hits"]


def test_g36_transition_check_appends_audit_log(isolated_data_root):
    """§10.2-36：每次判定状态跃迁必须写入审计日志。"""
    gate.check(_valid_task(current_state="READY", next_state="RUNNING"))
    log_path = isolated_data_root / "agent_state" / "audit_log.jsonl"
    assert log_path.exists()
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records) == 1
    assert records[0]["event"]["kind"] == "gate.transition_check"
    assert records[0]["event"]["source"] == "READY"
    assert records[0]["event"]["target"] == "RUNNING"
    assert records[0]["event"]["legal"] is True


def test_g37_blocks_when_awaiting_user_decision_and_not_paused():
    """必测项：G37 命中——等待用户决策 + 任务未暂停 → 阻断。"""
    result = gate.check(
        _valid_task(awaiting_user_decision=True, is_paused=False, current_state="RUNNING")
    )
    assert "G37" in result["hits"], result["reasons"]
    assert result["blocked"] is True


def test_g37_not_hit_when_awaiting_user_decision_and_paused():
    """必测项：G37 不命中——等待用户决策 + 已暂停 → 不阻断。"""
    result = gate.check(
        _valid_task(awaiting_user_decision=True, is_paused=True, current_state="RUNNING")
    )
    assert "G37" not in result["hits"], result["reasons"]


def test_g37_not_hit_when_paused_by_state_without_is_paused_flag():
    """契约认可 ``current_state in {BLOCKED, CHANGE_PENDING, ESCALATED}`` 即为暂停。"""
    result = gate.check(
        _valid_task(awaiting_user_decision=True, is_paused=False, current_state="BLOCKED")
    )
    assert "G37" not in result["hits"], result["reasons"]


def test_g37_not_hit_when_no_user_decision_pending():
    result = gate.check(_valid_task(awaiting_user_decision=False, is_paused=False))
    assert "G37" not in result["hits"]


def test_gate_rule_ids_cover_all_seven_gates():
    assert gate.GATE_RULES == ("G31", "G32", "G33", "G34", "G35", "G36", "G37")


def test_package_workspace_base_and_default_data_root_are_correct():
    """回归：工作区基址必须是 ``E:\\Socratic_M``，默认数据根必须是 ``<基址>/team``。"""
    import control

    assert control.WORKSPACE_BASE.name == "Socratic_M"
    assert control.DEFAULT_DATA_ROOT == control.WORKSPACE_BASE / "team"
    assert (control.DEFAULT_DATA_ROOT / "milestones" / "M1" / "TASK-001-v2.md").exists()


def test_gate_hits_are_ordered_and_reasons_match():
    result = gate.check(
        _valid_task(
            contract_status="DRAFT",
            task_version="v1",
            dependencies_met=False,
            change_pending=True,
            affected_by_change=True,
            budget_exceeded=True,
            awaiting_user_decision=True,
            is_paused=False,
            current_state="SUBMITTED",
            next_state="RUNNING",
        )
    )
    assert result["hits"] == ["G31", "G32", "G34", "G35", "G36", "G37"]
    assert len(result["reasons"]) == len(result["hits"])
    assert [reason.split("]")[0].strip("[") for reason in result["reasons"]] == result["hits"]


def test_gate_rejects_non_dict_task():
    with pytest.raises(TypeError):
        gate.check(["not", "a", "dict"])


def test_gate_source_cites_baseline_defect_v1_4_err_01():
    """契约 must_do#1：实现跃迁表时必须引用 ``V1.4-ERR-01``，不得单独引用 §10.1。"""
    source = (Path(gate.__file__)).read_text(encoding="utf-8")
    assert "V1.4-ERR-01" in source


# ================================================================ 勘误 01 · V1.4-ERR-02
# 授权文件：team/milestones/M1/TASK-001-v2-ERRATUM-01.md（Owner 裁决 O-2：只补跃迁表）


def test_g36_erratum_accepts_rework_to_running():
    """勘误 2.2：``REWORK → RUNNING``（返工完成，§9.2/§9.3）为合法。"""
    result = gate.check(_valid_task(current_state="REWORK", next_state="RUNNING"))
    assert "G36" not in result["hits"], result["reasons"]


def test_g36_erratum_accepts_escalated_to_ready():
    """勘误 2.2：``ESCALATED → READY``（用户裁决继续，§6.1）为合法。"""
    result = gate.check(_valid_task(current_state="ESCALATED", next_state="READY"))
    assert "G36" not in result["hits"], result["reasons"]


def test_g36_erratum_accepts_escalated_to_blocked():
    """勘误 2.2：``ESCALATED → BLOCKED``（用户裁决暂停/需补条件，§6.5）为合法。"""
    result = gate.check(_valid_task(current_state="ESCALATED", next_state="BLOCKED"))
    assert "G36" not in result["hits"], result["reasons"]


def test_g36_erratum_transition_set_is_exactly_v2_plus_three():
    """范围证据：跃迁集合必须**恰好**等于 v2 原集合 + 勘误三条，一条不多、一条不少。"""
    v2_set = {
        ("DRAFT", "AUDITING"),
        ("AUDITING", "READY"),
        ("READY", "RUNNING"),
        ("RUNNING", "SUBMITTED"),
        ("SUBMITTED", "ACCEPTED"),
        ("RUNNING", "REWORK"),
        ("RUNNING", "BLOCKED"),
        ("RUNNING", "CHANGE_PENDING"),
        ("RUNNING", "ESCALATED"),
        ("SUBMITTED", "REWORK"),
        ("SUBMITTED", "BLOCKED"),
        ("SUBMITTED", "CHANGE_PENDING"),
        ("SUBMITTED", "ESCALATED"),
        ("CHANGE_PENDING", "READY"),
        ("DRAFT", "BLOCKED"),
        ("AUDITING", "BLOCKED"),
        ("READY", "BLOCKED"),
    }
    erratum_added = {
        ("REWORK", "RUNNING"),
        ("ESCALATED", "READY"),
        ("ESCALATED", "BLOCKED"),
    }
    assert gate.LEGAL_TRANSITIONS == frozenset(v2_set | erratum_added)


def test_g36_erratum_does_not_liberalize_other_dead_ends():
    """勘误只开口三条：``ACCEPTED`` 仍为终态，``REWORK``/``ESCALATED`` 其余出边仍非法。"""
    assert "G36" in gate.check(_valid_task(current_state="ACCEPTED", next_state="READY"))["hits"]
    assert "G36" in gate.check(_valid_task(current_state="REWORK", next_state="SUBMITTED"))["hits"]
    assert "G36" in gate.check(_valid_task(current_state="REWORK", next_state="ACCEPTED"))["hits"]
    assert "G36" in gate.check(_valid_task(current_state="ESCALATED", next_state="RUNNING"))["hits"]
    assert "G36" in gate.check(_valid_task(current_state="ESCALATED", next_state="ACCEPTED"))["hits"]


def test_g36_erratum_keeps_submitted_to_running_illegal():
    """勘误不得放宽其他规则——原 ``SUBMITTED → RUNNING`` 非法判定必须保留。"""
    result = gate.check(_valid_task(current_state="SUBMITTED", next_state="RUNNING"))
    assert "G36" in result["hits"]
    assert result["blocked"] is True


def test_gate_source_cites_baseline_defect_v1_4_err_02():
    """勘误第三节第 2 条：实现必须在注释中引用 ``V1.4-ERR-02``。"""
    source = (Path(gate.__file__)).read_text(encoding="utf-8")
    assert "V1.4-ERR-02" in source


# ================================================================ objection


def _submit(**overrides):
    payload = {
        "task_id": "TASK-001",
        "version": "v2",
        "kind": "cannot_execute",
        "claim": "契约前提无法执行",
        "evidence": "见复现步骤",
        "invalidates": "M1 里程碑 objective",
    }
    payload.update(overrides)
    return objection.submit(**payload)


def test_objection_id_is_generated_with_expected_format():
    result = _submit()
    assert re.fullmatch(r"OBJ-\d{8}-\d{3}", result["obj_id"]), result["obj_id"]


def test_objection_id_increments_per_submission():
    first = _submit()
    second = _submit()
    assert first["obj_id"].endswith("001")
    assert second["obj_id"].endswith("002")
    assert first["obj_id"][:12] == second["obj_id"][:12]  # 同一日期前缀


def test_objection_id_cannot_be_supplied_externally():
    """必测项（ARC-001 D1）：调用方传 ID 一律报错。"""
    with pytest.raises(TypeError):
        objection.submit(
            task_id="TASK-001",
            version="v2",
            kind="cannot_execute",
            claim="c",
            evidence="e",
            invalidates="unknown",
            obj_id="OBJ-20260930-999",
        )


def test_objection_submit_has_no_obj_id_parameter():
    import inspect

    params = list(inspect.signature(objection.submit).parameters)
    assert params == ["task_id", "version", "kind", "claim", "evidence", "invalidates"]


def test_objection_submit_sets_task_blocked(isolated_data_root):
    result = _submit()
    assert result["status"] == "BLOCKED"
    state = json.loads((isolated_data_root / "agent_state" / "task_state.json").read_text(encoding="utf-8"))
    assert state["tasks"]["TASK-001"]["status"] == "BLOCKED"


def test_objection_submit_records_previous_status(isolated_data_root):
    _write_task_state(isolated_data_root, "TASK-001", "READY")
    result = _submit()
    assert result["task_previous_status"] == "READY"
    state = json.loads((isolated_data_root / "agent_state" / "task_state.json").read_text(encoding="utf-8"))
    assert state["tasks"]["TASK-001"]["previous_status"] == "READY"


def test_objection_writes_record_into_proposals(isolated_data_root):
    result = _submit()
    record = isolated_data_root / "proposals" / f"{result['obj_id']}.md"
    assert record.exists()
    body = record.read_text(encoding="utf-8")
    assert result["obj_id"] in body
    assert "契约前提无法执行" in body
    assert "§8.4" in body


def test_objection_appends_index_entry(isolated_data_root):
    result = _submit()
    index = json.loads((isolated_data_root / "agent_state" / "objection_index.json").read_text(encoding="utf-8"))
    entry = index["objections"][-1]
    assert entry["obj_id"] == result["obj_id"]
    assert entry["status"] == "BLOCKED"
    assert entry["needs_mentor_verdict_plan"] is False
    assert entry["mentor_response_registered"] is False


def test_objection_invalidates_unknown_sets_mentor_verdict_plan_flag(isolated_data_root):
    result = _submit(invalidates="unknown")
    assert result["invalidates_unknown"] is True
    assert result["needs_mentor_verdict_plan"] is True
    index = json.loads((isolated_data_root / "agent_state" / "objection_index.json").read_text(encoding="utf-8"))
    assert index["objections"][-1]["needs_mentor_verdict_plan"] is True


def test_objection_invalidates_is_required():
    with pytest.raises(ValueError):
        _submit(invalidates=None)
    with pytest.raises(ValueError):
        _submit(invalidates=[])


def test_objection_kind_is_validated():
    with pytest.raises(ValueError):
        _submit(kind="better_idea")


def test_objection_rejects_empty_claim_or_evidence():
    with pytest.raises(ValueError):
        _submit(claim="   ")
    with pytest.raises(ValueError):
        _submit(evidence="")


def test_objection_submit_appends_audit_log(isolated_data_root):
    _submit()
    log_path = isolated_data_root / "agent_state" / "audit_log.jsonl"
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert records[-1]["event"]["kind"] == "objection.submit"
    assert records[-1]["event"]["target"] == "BLOCKED"


def test_mentor_response_is_registered(isolated_data_root):
    result = _submit()
    registered = objection.register_mentor_response(result["obj_id"], "已阅，按原决策继续。")
    assert registered["mentor_response_registered"] is True
    body = (isolated_data_root / "proposals" / f"{result['obj_id']}.md").read_text(encoding="utf-8")
    assert "已阅，按原决策继续。" in body
    index = json.loads((isolated_data_root / "agent_state" / "objection_index.json").read_text(encoding="utf-8"))
    entry = index["objections"][-1]
    assert entry["mentor_response"] == "已阅，按原决策继续。"
    assert entry["mentor_response_registered"] is True


def test_mentor_response_registered_even_when_objection_judged_minor(isolated_data_root):
    """§8.6：异议被判「不重大」时，回应仍必须登记（留痕不因结果而豁免）。"""
    obj_id = _submit()["obj_id"]
    _open_both(obj_id, major_mentor=False, major_controller=False)
    assert dual_judge.compare(obj_id) == {"outcome": "both_minor", "escalate": False}

    registered = objection.register_mentor_response(obj_id, "判定不重大，回应仍登记。")
    assert registered["mentor_response_registered"] is True


def test_register_mentor_response_requires_known_objection():
    with pytest.raises(KeyError):
        objection.register_mentor_response("OBJ-20260930-404", "回应")


def test_register_mentor_response_rejects_empty_text():
    obj_id = _submit()["obj_id"]
    with pytest.raises(ValueError):
        objection.register_mentor_response(obj_id, "   ")


# ================================================================ dual_judge


def _verdict(major, rules=(), scope="无"):
    return {"major": major, "hit_rules": list(rules), "scope": scope}


def _sealed_file(root, obj_id, role):
    return root / "agent_state" / "verdicts" / f"{obj_id}.{role}.sealed.json"


def _opened_file(root, obj_id, role):
    return root / "agent_state" / "verdicts" / f"{obj_id}.{role}.json"


def _open_both(obj_id, *, major_mentor=True, major_controller=True, rules_mentor=(1,), rules_controller=(1,)):
    """封存双方 + 揭示双方 → 开封（新语义下的完整链路）。"""
    dual_judge.seal_verdict("mentor", obj_id, _verdict(major_mentor, rules_mentor))
    dual_judge.seal_verdict("controller", obj_id, _verdict(major_controller, rules_controller))
    dual_judge.reveal_verdict("mentor", obj_id, _verdict(major_mentor, rules_mentor))
    dual_judge.reveal_verdict("controller", obj_id, _verdict(major_controller, rules_controller))


def test_seal_writes_only_commitment_and_no_plaintext(isolated_data_root):
    """封存文件**只含**承诺等 6 个字段；判决明文一个字都不在盘上。"""
    obj_id = _submit()["obj_id"]
    marker = "SEAL-MARKER-plaintext-must-never-hit-disk"
    result = dual_judge.seal_verdict("mentor", obj_id, {"major": True, "hit_rules": [1], "scope": marker})

    raw = _sealed_file(isolated_data_root, obj_id, "mentor").read_text(encoding="utf-8")
    sealed = json.loads(raw)
    assert set(sealed) == {"obj_id", "role", "commitment", "salt", "sealed_at", "state"}
    assert sealed["state"] == "SEALED"
    assert marker not in raw                      # 明文不在盘上
    assert "major" not in raw and "scope" not in raw
    assert result["commitment"] == sealed["commitment"] == dual_judge.commitment_of(sealed["salt"],
        {"major": True, "hit_rules": [1], "scope": marker})


def test_read_verdict_denied_after_single_seal(isolated_data_root):
    """必测项：只封存一方时读取被拒（SealedVerdict）。"""
    obj_id = _submit()["obj_id"]
    dual_judge.seal_verdict("mentor", obj_id, _verdict(True, (1,)))
    with pytest.raises(SealedVerdict):
        dual_judge.read_verdict("mentor", obj_id)
    with pytest.raises(SealedVerdict):
        dual_judge.read_verdict("controller", obj_id)


def test_read_verdict_denied_after_both_seals_without_reveal(isolated_data_root):
    """必测项：双份封存但未揭示 → 读取仍被拒。"""
    obj_id = _submit()["obj_id"]
    dual_judge.seal_verdict("mentor", obj_id, _verdict(True, (1,)))
    dual_judge.seal_verdict("controller", obj_id, _verdict(True, (1,)))
    with pytest.raises(SealedVerdict):
        dual_judge.read_verdict("mentor", obj_id)
    assert not _opened_file(isolated_data_root, obj_id, "mentor").exists()
    assert not _opened_file(isolated_data_root, obj_id, "controller").exists()


def test_compare_denied_after_single_seal(isolated_data_root):
    """必测项：只封存一方就 compare → SealedVerdict。"""
    obj_id = _submit()["obj_id"]
    dual_judge.seal_verdict("mentor", obj_id, _verdict(True, (1,)))
    with pytest.raises(SealedVerdict):
        dual_judge.compare(obj_id)


def test_compare_denied_after_both_seals_without_reveal(isolated_data_root):
    obj_id = _submit()["obj_id"]
    dual_judge.seal_verdict("mentor", obj_id, _verdict(True, (1,)))
    dual_judge.seal_verdict("controller", obj_id, _verdict(True, (1,)))
    with pytest.raises(SealedVerdict):
        dual_judge.compare(obj_id)


def test_reveal_denied_without_seal(isolated_data_root):
    """必测项：未封存就揭示 → SealedVerdict。"""
    obj_id = _submit()["obj_id"]
    with pytest.raises(SealedVerdict):
        dual_judge.reveal_verdict("mentor", obj_id, _verdict(True, (1,)))
    assert not _sealed_file(isolated_data_root, obj_id, "mentor").exists()


def test_reveal_with_wrong_verdict_raises_mismatch_and_writes_no_plaintext(isolated_data_root):
    """必测项：揭示内容与承诺不符 → CommitmentMismatch，且**不落任何明文**。"""
    obj_id = _submit()["obj_id"]
    dual_judge.seal_verdict("mentor", obj_id, _verdict(True, (1,), "原始判定"))
    dual_judge.seal_verdict("controller", obj_id, _verdict(True, (1,), "原始判定"))

    with pytest.raises(CommitmentMismatch):
        dual_judge.reveal_verdict("mentor", obj_id, _verdict(False, (2,), "被篡改的判定"))

    verdicts_dir = isolated_data_root / "agent_state" / "verdicts"
    assert not _opened_file(isolated_data_root, obj_id, "mentor").exists()
    assert not _opened_file(isolated_data_root, obj_id, "controller").exists()
    assert not (verdicts_dir / f"{obj_id}.comparison.json").exists()
    # 封存状态也不得被这次失败改动
    assert json.loads(_sealed_file(isolated_data_root, obj_id, "mentor").read_text(encoding="utf-8"))["state"] == "SEALED"


def test_single_reveal_writes_no_plaintext(isolated_data_root):
    """必测项：只有一方揭示时，磁盘上不得出现任何明文。"""
    obj_id = _submit()["obj_id"]
    dual_judge.seal_verdict("mentor", obj_id, _verdict(True, (1,)))
    dual_judge.seal_verdict("controller", obj_id, _verdict(True, (1,)))
    dual_judge.reveal_verdict("mentor", obj_id, _verdict(True, (1,)))

    verdicts_dir = isolated_data_root / "agent_state" / "verdicts"
    assert not _opened_file(isolated_data_root, obj_id, "mentor").exists()
    assert not _opened_file(isolated_data_root, obj_id, "controller").exists()
    assert not (verdicts_dir / f"{obj_id}.comparison.json").exists()
    with pytest.raises(SealedVerdict):
        dual_judge.read_verdict("mentor", obj_id)
    # 封存文件本身仍只含承诺（无明文）
    raw = _sealed_file(isolated_data_root, obj_id, "mentor").read_text(encoding="utf-8")
    assert '"major"' not in raw and "hit_rules" not in raw


def test_read_verdict_available_only_after_both_reveals(isolated_data_root):
    obj_id = _submit()["obj_id"]
    _open_both(obj_id, major_mentor=True, major_controller=False)
    mentor = dual_judge.read_verdict("mentor", obj_id)
    controller = dual_judge.read_verdict("controller", obj_id)
    assert mentor["verdict"]["major"] is True
    assert controller["verdict"]["major"] is False
    assert mentor["state"] == "OPENED" and controller["state"] == "OPENED"


def test_reveal_is_idempotent(isolated_data_root):
    obj_id = _submit()["obj_id"]
    dual_judge.seal_verdict("mentor", obj_id, _verdict(True, (1,)))
    dual_judge.seal_verdict("controller", obj_id, _verdict(True, (1,)))
    dual_judge.reveal_verdict("mentor", obj_id, _verdict(True, (1,)))
    dual_judge.reveal_verdict("mentor", obj_id, _verdict(True, (1,)))   # 幂等
    dual_judge.reveal_verdict("controller", obj_id, _verdict(True, (1,)))
    assert dual_judge.compare(obj_id) == {"outcome": "both_major", "escalate": True}


def test_reseal_is_rejected(isolated_data_root):
    """封存不可覆盖：防止开封后偷换结论。"""
    obj_id = _submit()["obj_id"]
    dual_judge.seal_verdict("mentor", obj_id, _verdict(True, (1,)))
    with pytest.raises(AlreadySealed):
        dual_judge.seal_verdict("mentor", obj_id, _verdict(False, (), "换一个"))


def test_sealed_verdict_is_also_incomplete_judgment(isolated_data_root):
    """兼容 v2 验收项 5：``except IncompleteJudgment`` 仍能捕获新的 SealedVerdict。"""
    obj_id = _submit()["obj_id"]
    dual_judge.seal_verdict("mentor", obj_id, _verdict(True, (1,)))
    with pytest.raises(IncompleteJudgment):
        dual_judge.compare(obj_id)


def test_compare_both_major_escalates():
    obj_id = _submit()["obj_id"]
    _open_both(obj_id, major_mentor=True, major_controller=True)
    assert dual_judge.compare(obj_id) == {"outcome": "both_major", "escalate": True}


def test_compare_both_minor_does_not_escalate():
    obj_id = _submit()["obj_id"]
    _open_both(obj_id, major_mentor=False, major_controller=False, rules_mentor=(), rules_controller=())
    assert dual_judge.compare(obj_id) == {"outcome": "both_minor", "escalate": False}


def test_compare_divergent_escalates():
    obj_id = _submit()["obj_id"]
    _open_both(obj_id, major_mentor=True, major_controller=False)
    assert dual_judge.compare(obj_id) == {"outcome": "divergent", "escalate": True}


def test_opened_verdicts_are_written_to_separate_files(isolated_data_root):
    obj_id = _submit()["obj_id"]
    _open_both(obj_id)
    assert _opened_file(isolated_data_root, obj_id, "mentor").exists()
    assert _opened_file(isolated_data_root, obj_id, "controller").exists()
    assert _sealed_file(isolated_data_root, obj_id, "mentor").exists()


def test_comparison_is_archived_with_both_verdicts(isolated_data_root):
    obj_id = _submit()["obj_id"]
    _open_both(obj_id, major_mentor=True, major_controller=False,
               rules_mentor=(3,), rules_controller=())
    dual_judge.compare(obj_id)
    archived = json.loads(
        (isolated_data_root / "agent_state" / "verdicts" / f"{obj_id}.comparison.json").read_text(encoding="utf-8")
    )
    assert archived["outcome"] == "divergent"
    assert archived["mentor_verdict"]["hit_rules"] == [3]
    assert archived["controller_verdict"]["major"] is False


def test_submit_verdict_alias_now_seals_without_plaintext(isolated_data_root):
    """旧名 ``submit_verdict`` 语义已改为封存：不再直接落明文。"""
    obj_id = _submit()["obj_id"]
    dual_judge.submit_verdict("mentor", obj_id, _verdict(True, (1,), "别名封存"))
    raw = _sealed_file(isolated_data_root, obj_id, "mentor").read_text(encoding="utf-8")
    assert "别名封存" not in raw
    assert not _opened_file(isolated_data_root, obj_id, "mentor").exists()


def test_seal_rejects_unknown_role():
    obj_id = _submit()["obj_id"]
    with pytest.raises(ValueError):
        dual_judge.seal_verdict("owner", obj_id, _verdict(True, ()))


def test_seal_rejects_malformed_verdict():
    obj_id = _submit()["obj_id"]
    with pytest.raises(ValueError):
        dual_judge.seal_verdict("mentor", obj_id, {"major": True})
    with pytest.raises(ValueError):
        dual_judge.seal_verdict("mentor", obj_id, {"major": "yes", "hit_rules": [], "scope": "无"})


def test_compare_rejects_empty_obj_id():
    with pytest.raises(ValueError):
        dual_judge.compare("  ")


# ================================================================ audit_log


def _read_log(root: Path):
    path = root / "agent_state" / "audit_log.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_audit_log_append_returns_hash_and_creates_file(isolated_data_root):
    event_hash = audit_log.append({"kind": "unit-test", "n": 1})
    assert re.fullmatch(r"[0-9a-f]{64}", event_hash)
    assert (isolated_data_root / "agent_state" / "audit_log.jsonl").exists()


def test_audit_log_verify_true_for_untampered_event(isolated_data_root):
    first = audit_log.append({"kind": "unit-test", "n": 1})
    second = audit_log.append({"kind": "unit-test", "n": 2})
    assert audit_log.verify(first) is True
    assert audit_log.verify(second) is True


def test_audit_log_chains_prev_hash(isolated_data_root):
    first = audit_log.append({"kind": "unit-test", "n": 1})
    audit_log.append({"kind": "unit-test", "n": 2})
    records = _read_log(isolated_data_root)
    assert records[0]["prev_hash"] == audit_log.GENESIS
    assert records[1]["prev_hash"] == first
    assert records[0]["seq"] == 1 and records[1]["seq"] == 2


def test_audit_log_verify_false_after_tamper(isolated_data_root):
    """验收项 7：篡改后 ``verify`` 必须返回 False。"""
    first = audit_log.append({"kind": "unit-test", "n": 1})
    second = audit_log.append({"kind": "unit-test", "n": 2})
    assert audit_log.verify(second) is True

    path = isolated_data_root / "agent_state" / "audit_log.jsonl"
    records = _read_log(isolated_data_root)
    records[0]["event"]["n"] = 999  # 篡改第一条事件的正文
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                  for record in records) + "\n",
        encoding="utf-8",
    )

    assert audit_log.verify(first) is False
    assert audit_log.verify(second) is False  # 链式失效向后传播


def test_audit_log_verify_false_for_unknown_hash(isolated_data_root):
    audit_log.append({"kind": "unit-test", "n": 1})
    assert audit_log.verify("f" * 64) is False


def test_audit_log_verify_false_when_log_missing(isolated_data_root):
    assert audit_log.verify("a" * 64) is False


def test_audit_log_append_rejects_non_dict():
    with pytest.raises(TypeError):
        audit_log.append("not-a-dict")


def test_audit_log_append_is_append_only(isolated_data_root):
    audit_log.append({"kind": "unit-test", "n": 1})
    audit_log.append({"kind": "unit-test", "n": 2})
    records = _read_log(isolated_data_root)
    assert [record["event"]["n"] for record in records] == [1, 2]
