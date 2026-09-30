"""§10.2 七条门禁（G31–G37）的静态字典校验 —— TASK-001 v2 · must_do #1。

契约：``team/milestones/M1/TASK-001-v2.md``（唯一有效版本）
需求：``REQ-T1``／上游 ``L0-06``（程序化权限隔离，而非仅依赖提示词）

职责边界（``bounded_scope`` / ARC-001 D4）
------------------------------------------
本模块只做**静态字典校验**：接收一个 ``task`` 字典，返回命中规则与是否阻断。
它**不真正拦截工具调用**。真正的拦截需要 hook 到工具执行层，属 P1 之后。
⚠ 该简化必须在验收时显式声明，不得让本版看起来像「门禁已经能拦住越权」。

输入字段
--------
【契约明文定义】``contract_status`` · ``contract_version`` · ``task_version`` ·
``allowed_paths`` · ``dependencies_met`` · ``change_pending`` · ``affected_by_change`` ·
``budget_exceeded`` · ``rework_count`` · ``rework_limit`` · ``risk_over_threshold`` ·
``awaiting_user_decision`` · ``current_state`` · ``is_paused``

【本实现补充（契约未规定字段名，已在工作报告中登记为设计决策）】
``next_state`` / ``to_state``（跃迁目标状态，二者取其一）· ``write_paths``（本次拟写入路径）·
``report``（§8.1-30 执行报告）· ``blocked_from``（``BLOCKED → 原状态`` 时的来源状态，可选）

返回
----
``{"hits": [规则号...], "blocked": bool, "reasons": [...]}``
``hits`` 与 ``reasons`` 按 G31→G37 顺序一一对应；命中任一规则即 ``blocked=True``
（G33 的语义是「不得进入 PASS」、G35 的语义是「触发暂停/升级」，其具体语义写在 ``reasons`` 中）。
"""

from __future__ import annotations

import posixpath
import re

from . import audit_log, WORKSPACE_BASE

__all__ = [
    "check",
    "LEGAL_TRANSITIONS",
    "BLOCKED_ENTRY_STATES",
    "PAUSED_STATES",
    "REQUIRED_REPORT_ITEMS",
    "GATE_RULES",
    "DEFAULT_ALLOWED_PATHS",
]

#: 七条门禁的规则号（§10.2-31 ～ §10.2-37）
GATE_RULES = ("G31", "G32", "G33", "G34", "G35", "G36", "G37")

#: 契约「路径口径」给出的 allowed_paths（task 未提供该字段时用作默认白名单）
DEFAULT_ALLOWED_PATHS = (
    "team/src/",
    "team/tests/",
    "team/evidence/TASK-001-run.log",
)

#: G37 认可的「暂停」状态（契约 must_do#1 明文枚举）
PAUSED_STATES = frozenset({"BLOCKED", "CHANGE_PENDING", "ESCALATED"})

#: §10.1 状态集合（契约跃迁表出现过的全部状态）
ALL_STATES = frozenset(
    {
        "DRAFT",
        "AUDITING",
        "READY",
        "RUNNING",
        "SUBMITTED",
        "ACCEPTED",
        "REWORK",
        "BLOCKED",
        "CHANGE_PENDING",
        "ESCALATED",
    }
)

#: 可进入 BLOCKED 的源状态集合（用于 ``BLOCKED → 进入 BLOCKED 前的状态`` 的合法性反推）
BLOCKED_ENTRY_STATES = frozenset({"DRAFT", "AUDITING", "READY", "RUNNING", "SUBMITTED"})

#: 合法状态跃迁表 —— TASK-001 v2「合法状态跃迁表」逐行硬编码（ARC-001 D5：不做配置化）
#:
#: ⚠ V1.4-ERR-01（见 ``team/architecture/BASELINE-DEFECTS.md``）：
#: V1.4 §8.4 要求「异议提交即任务进入 BLOCKED」，但 §10.1 的跃迁表只允许
#: ``RUNNING|SUBMITTED → BLOCKED``，导致**启动期异议无法停机**。
#: 以下 ``DRAFT|AUDITING|READY → BLOCKED`` 与 ``BLOCKED → 进入前的状态`` 两条，
#: 依据 V1.4-ERR-01 由 TASK-001 v2 追加，**不得单独引用 §10.1**。
LEGAL_TRANSITIONS = frozenset(
    {
        # §10.1 原有跃迁
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
        # v2 追加（V1.4-ERR-01）：启动期异议提交
        ("DRAFT", "BLOCKED"),
        ("AUDITING", "BLOCKED"),
        ("READY", "BLOCKED"),
        # 注：``BLOCKED → 进入 BLOCKED 前的状态`` 依赖来源状态，见 _transition_legal()
        # 勘误 01 追加（V1.4-ERR-02）：补齐 REWORK / ESCALATED 的死端
        #
        # ⚠ V1.4-ERR-02（见 ``team/architecture/BASELINE-DEFECTS.md``）：
        # §10.1 的跃迁表只写了「主流水线 + 异常分支的进入边」，
        # ``REWORK`` 与 ``ESCALATED`` 没有任何出边，属**死端**——
        # 第一次返工（§9.2/§9.3）或第一次升级（§9.2 → §6 用户决策）后任务永久卡死。
        # 以下三条依 ``TASK-001-v2-ERRATUM-01.md``（Owner 裁决 O-2：只补跃迁表）追加；
        # ``ACCEPTED`` 仍为终态，不在本勘误范围内。
        # 引用 §10.1 的实现必须同时引用 ERR-01 与 ERR-02，不得单独引用 §10.1。
        ("REWORK", "RUNNING"),  # 返工完成，重新进入执行（§9.2、§9.3）
        ("ESCALATED", "READY"),  # 用户裁决「继续」（§6.1、§9.2）
        ("ESCALATED", "BLOCKED"),  # 用户裁决「暂停 / 需补充条件」（§6.5、§9.2）
    }
)

#: §8.1-30 要求执行报告包含的五项
REQUIRED_REPORT_ITEMS = ("changes", "test_evidence", "risks", "deviations", "rollback")

#: 五项证据的中文名（用于 reasons）
REPORT_ITEM_LABELS = {
    "changes": "真实变更",
    "test_evidence": "测试证据",
    "risks": "风险",
    "deviations": "偏差",
    "rollback": "回滚点",
}

#: 五项证据可接受的键名（含中英文别名）
REPORT_ITEM_ALIASES = {
    "changes": ("changes", "change", "变更", "真实变更"),
    "test_evidence": ("test_evidence", "test", "tests", "测试证据", "测试"),
    "risks": ("risks", "risk", "风险"),
    "deviations": ("deviations", "deviation", "偏差"),
    "rollback": ("rollback", "rollback_point", "回滚点", "回滚"),
}


# ---------------------------------------------------------------- 工具函数


def _as_list(value):
    """把标量/list/tuple/set 统一成 list（``None`` → 空列表）。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value]


def _is_present(value) -> bool:
    """判断报告项是否「有内容」（``None``/空串/空容器均视为缺失）。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return len(value) > 0
    return True


def _missing_report_items(report) -> list:
    """返回执行报告中缺失的 §8.1-30 证据项（用规范名）。"""
    missing = []
    if not isinstance(report, dict):
        return list(REQUIRED_REPORT_ITEMS)
    for item in REQUIRED_REPORT_ITEMS:
        found = any(_is_present(report.get(alias)) for alias in REPORT_ITEM_ALIASES[item])
        if not found:
            missing.append(item)
    return missing


def _normalize_rel(path) -> str:
    """把路径规范化为相对工作区基址的 posix 相对路径；越出工作区返回 ``""``。"""
    text = str(path).strip().replace("\\", "/")
    if not text:
        return ""
    base = str(WORKSPACE_BASE).replace("\\", "/").rstrip("/")
    if re.match(r"^[A-Za-z]:", text) or text.startswith("//"):
        if text.lower() == base.lower():
            return ""
        if text.lower().startswith(base.lower() + "/"):
            text = text[len(base) + 1 :]
        else:
            return ""  # 工作区之外的绝对路径
    text = posixpath.normpath(text).lstrip("/")
    if text in (".", "..") or text.startswith("../"):
        return ""  # 归一化后逃出工作区
    return text


def _path_in_scope(path, allowed_paths) -> bool:
    """判断某写入路径是否落在 allowed_paths 内（Windows 下大小写不敏感）。"""
    rel = _normalize_rel(path)
    if not rel:
        return False
    for entry in _as_list(allowed_paths):
        raw = str(entry).strip().replace("\\", "/")
        is_dir = raw.endswith("/")
        norm = _normalize_rel(raw)
        if not norm:
            continue
        if is_dir:
            if rel.lower() == norm.lower() or rel.lower().startswith(norm.lower() + "/"):
                return True
        elif rel.lower() == norm.lower():
            return True
    return False


def _is_paused(task: dict) -> bool:
    """G37 的「已暂停」判定：``is_paused == True`` 或 ``current_state`` 属暂停态。"""
    if task.get("is_paused") is True:
        return True
    return task.get("current_state") in PAUSED_STATES


def _transition_legal(source: str, target: str, blocked_from=None):
    """校验状态跃迁是否合法，返回 ``(legal, 说明)``。"""
    if (source, target) in LEGAL_TRANSITIONS:
        return True, "§10.1 / TASK-001 v2 跃迁表命中"

    # BLOCKED → 进入 BLOCKED 前的状态（v2 追加，依据 V1.4-ERR-01）
    #
    # 契约把合法目标表述为「进入 BLOCKED 前的状态」，但未规定该来源状态的输入字段。
    # 本实现按「哪些状态可以进入 BLOCKED」反推合法目标集合（BLOCKED_ENTRY_STATES）；
    # 若调用方提供了 blocked_from，则追加一致性校验。
    # 该补充已在工作报告中登记为设计决策，不改变契约的语义。
    if source == "BLOCKED":
        if target in BLOCKED_ENTRY_STATES:
            if blocked_from is None:
                return True, "BLOCKED → 进入 BLOCKED 前的状态（未提供 blocked_from，按可进入 BLOCKED 的状态集合校验）"
            if str(blocked_from) == target:
                return True, "BLOCKED → 进入 BLOCKED 前的状态（blocked_from 与目标一致）"
            return False, f"blocked_from={blocked_from!r} 与目标状态 {target!r} 不一致"
        return False, (
            "BLOCKED 只能回到进入 BLOCKED 前的状态（"
            + "|".join(sorted(BLOCKED_ENTRY_STATES))
            + "），不能直接进入 " + target
        )

    if target not in ALL_STATES:
        return False, f"目标状态 {target!r} 不在 §10.1 状态集合内"
    return False, f"TASK-001 v2 跃迁表未列举 {source} → {target}（「其余一律非法」）"


# ---------------------------------------------------------------- 主入口


def check(task: dict) -> dict:
    """对 ``task`` 字典执行 §10.2 的七条门禁校验（静态、确定性、无副作用*）。

    *唯一的副作用：判定状态跃迁时按 §10.2-36 向审计日志追加一条事件（见 must_do#1 G36）。

    返回 ``{"hits": [...], "blocked": bool, "reasons": [...]}``。
    """
    if not isinstance(task, dict):
        raise TypeError(f"task 必须是 dict，收到 {type(task).__name__}")

    hits: list = []
    reasons: list = []

    def hit(rule: str, reason: str) -> None:
        hits.append(rule)
        reasons.append(f"[{rule}] {reason}")

    source = task.get("current_state")
    target = task.get("next_state", task.get("to_state"))

    # ---- G31 没有已批准契约不得启动任务（§10.2-31）
    contract_status = task.get("contract_status")
    if contract_status != "APPROVED":
        hit("G31", f"契约未获批准：contract_status={contract_status!r}，要求 'APPROVED' → 不得启动任务（§10.2-31）")

    # ---- G32 版本不匹配 / 超出文件权限 / 依赖未满足（§10.2-32）
    g32: list = []
    contract_version = task.get("contract_version")
    task_version = task.get("task_version")
    if contract_version != task_version:
        g32.append(f"版本不匹配：contract_version={contract_version!r} / task_version={task_version!r}")
    allowed_paths = task.get("allowed_paths", DEFAULT_ALLOWED_PATHS)
    for write_path in _as_list(task.get("write_paths")):
        if not _path_in_scope(write_path, allowed_paths):
            g32.append(f"写入路径超出 allowed_paths：{write_path!r}")
    if task.get("dependencies_met") is False:
        g32.append("依赖未满足：dependencies_met=False")
    if g32:
        hit("G32", "；".join(g32) + "（§10.2-32）")

    # ---- G33 执行报告缺少必要证据（§10.2-33 / §8.1-30）
    if "report" in task:
        missing = _missing_report_items(task.get("report"))
        if missing:
            labels = "、".join(f"{REPORT_ITEM_LABELS[m]}({m})" for m in missing)
            hit("G33", f"执行报告缺少 §8.1-30 必要证据项：{labels} → 不得进入 PASS（§10.2-33）")
    elif target in ("SUBMITTED", "ACCEPTED"):
        hit("G33", "未提交执行报告，§8.1-30 五项证据全部缺失 → 不得进入 PASS（§10.2-33）")

    # ---- G34 待批变更期间不得继续执行受影响任务（§10.2-34）
    if task.get("change_pending") is True and task.get("affected_by_change") is True:
        hit("G34", "存在待批变更且本任务受影响（change_pending + affected_by_change）→ 阻断（§10.2-34）")

    # ---- G35 预算 / 返工次数 / 风险阈值（§10.2-35）
    g35: list = []
    if task.get("budget_exceeded") is True:
        g35.append("预算已达上限（budget_exceeded=True）")
    rework_count = task.get("rework_count")
    rework_limit = task.get("rework_limit")
    if isinstance(rework_count, int) and isinstance(rework_limit, int) and rework_count > rework_limit:
        g35.append(f"返工次数超限（rework_count={rework_count} > rework_limit={rework_limit}）")
    if task.get("risk_over_threshold") is True:
        g35.append("风险达阈值（risk_over_threshold=True）")
    if g35:
        hit("G35", "；".join(g35) + " → 触发暂停/升级（§10.2-35）")

    # ---- G36 状态跃迁合法性 + 审计日志（§10.2-36 / V1.4-ERR-01）
    if isinstance(source, str) and source and isinstance(target, str) and target:
        legal, why = _transition_legal(source, target, task.get("blocked_from"))
        if not legal:
            hit("G36", f"状态跃迁非法：{source} → {target}；{why}（§10.2-36、V1.4-ERR-01）")
        # §10.2-36 后半句：所有状态跃迁由确定性程序校验「并写入不可随意覆盖的审计日志」
        audit_log.append(
            {
                "kind": "gate.transition_check",
                "task_id": task.get("task_id"),
                "contract_version": contract_version,
                "source": source,
                "target": target,
                "blocked_from": task.get("blocked_from"),
                "legal": legal,
                "detail": why,
                "basis": "§10.2-36 + V1.4-ERR-01 + TASK-001 v2 跃迁表",
            }
        )

    # ---- G37 等待用户决策期间未保持暂停（§6.5 / §10.2-37）
    if task.get("awaiting_user_decision") is True and not _is_paused(task):
        hit(
            "G37",
            "处于「等待用户决策」状态却未保持暂停："
            f"awaiting_user_decision=True, is_paused={task.get('is_paused')!r}, "
            f"current_state={source!r} → 阻断，不得自动放行、不得降级为单方决策（§6.5、§10.2-37）",
        )

    # hits / reasons 由 G31→G37 顺序构造，天然一一对应且顺序稳定
    return {"hits": hits, "blocked": bool(hits), "reasons": reasons}
