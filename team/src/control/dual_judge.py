"""§8.5 双判定提交与比对 —— TASK-001 v2 · must_do #3。

契约：``team/milestones/M1/TASK-001-v2.md``；基线 V1.4 §8.5 / §14 实现约束第 4 条。

为什么必须双方齐备才能比对（ARC-001 D2）
----------------------------------------
§8.5 要求 Mentor 与 Controller **独立判定、互不可见对方的结论**。
若允许先到先比，第二方就能通过读取比对结果反推第一方的结论，独立性被流程顺序吃掉。
故 :func:`compare` 在任一方未提交时抛 :class:`IncompleteJudgment`。

落盘
----
* 双方结论分别写入**不同文件**：``agent_state/verdicts/<obj_id>.<role>.json``
* 比对结果与双方原始结论**一并留档**：``agent_state/verdicts/<obj_id>.comparison.json``

限制（本版不含）
----------------
本程序只能保证**比对时不泄露**，无法阻止两个模型在**生成阶段**因同源而趋同——
后者是 G-8（环境限制），非本程序可解。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import data_root

__all__ = ["submit_verdict", "compare", "IncompleteJudgment", "ROLES", "SCHEMA"]

#: 判定主体（§8.5：Mentor 与 Controller 各判一次）
ROLES = ("mentor", "controller")

#: 记录结构标识（任务内 schema，非 DEF-06）
SCHEMA = "task-internal/v1"

_VERDICTS_DIR = ("agent_state", "verdicts")


class IncompleteJudgment(Exception):
    """双判定未齐备即请求比对时抛出（§8.5：双方均提交后方可比对）。"""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _verdicts_dir():
    return data_root().joinpath(*_VERDICTS_DIR)


def _verdict_path(obj_id: str, role: str):
    return _verdicts_dir() / f"{obj_id}.{role}.json"


def _comparison_path(obj_id: str):
    return _verdicts_dir() / f"{obj_id}.comparison.json"


def _save_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _load_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_verdict(verdict) -> dict:
    """校验 ``verdict`` 结构：``{"major": bool, "hit_rules": [...], "scope": str}``。"""
    if not isinstance(verdict, dict):
        raise ValueError(f"verdict 必须是 dict，收到 {type(verdict).__name__}")
    missing = [key for key in ("major", "hit_rules", "scope") if key not in verdict]
    if missing:
        raise ValueError(f"verdict 缺少必需字段：{missing}（应为 major/hit_rules/scope）")
    if not isinstance(verdict["major"], bool):
        raise ValueError(f"verdict['major'] 必须是 bool，收到 {type(verdict['major']).__name__}")
    if not isinstance(verdict["hit_rules"], (list, tuple)):
        raise ValueError("verdict['hit_rules'] 必须是列表")
    if not isinstance(verdict["scope"], str):
        raise ValueError("verdict['scope'] 必须是字符串")
    return {
        "major": verdict["major"],
        "hit_rules": list(verdict["hit_rules"]),
        "scope": verdict["scope"],
    }


def submit_verdict(role: str, obj_id: str, verdict: dict) -> dict:
    """提交某一方的重大性判定结论，落盘到该角色专属文件。

    ``role`` ∈ ``{"mentor", "controller"}``；``verdict`` = ``{"major","hit_rules","scope"}``。
    """
    if role not in ROLES:
        raise ValueError(f"role 必须是 {list(ROLES)} 之一，收到 {role!r}")
    if not isinstance(obj_id, str) or not obj_id.strip():
        raise ValueError(f"obj_id 必填且必须是非空字符串，收到 {obj_id!r}")
    obj_id = obj_id.strip()
    clean = _validate_verdict(verdict)

    submitted_at = _now()
    path = _verdict_path(obj_id, role)
    _save_json(
        path,
        {
            "schema": SCHEMA,
            "obj_id": obj_id,
            "role": role,
            "verdict": clean,
            "submitted_at": submitted_at,
        },
    )
    return {
        "obj_id": obj_id,
        "role": role,
        "verdict": clean,
        "verdict_path": str(path),
        "submitted_at": submitted_at,
    }


def compare(obj_id: str) -> dict:
    """在**双方均已提交**后比对两份结论，返回 ``{"outcome": ..., "escalate": ...}``。

    任一方缺失 → 抛 :class:`IncompleteJudgment`。
    ``both_major`` / ``divergent`` → ``escalate=True``；``both_minor`` → ``escalate=False``。
    比对结果与双方原始结论一并留档。
    """
    if not isinstance(obj_id, str) or not obj_id.strip():
        raise ValueError(f"obj_id 必填且必须是非空字符串，收到 {obj_id!r}")
    obj_id = obj_id.strip()

    missing = [role for role in ROLES if not _verdict_path(obj_id, role).exists()]
    if missing:
        raise IncompleteJudgment(
            f"双判定未齐备，缺少：{', '.join(missing)}。"
            "§8.5 要求双方独立判定、互不可见，双方均提交后方可比对（ARC-001 D2）。"
        )

    verdicts = {role: _load_json(_verdict_path(obj_id, role)) for role in ROLES}
    mentor_major = bool(verdicts["mentor"]["verdict"]["major"])
    controller_major = bool(verdicts["controller"]["verdict"]["major"])

    if mentor_major and controller_major:
        outcome, escalate = "both_major", True  # 升级 Owner（§6.1）
    elif not mentor_major and not controller_major:
        outcome, escalate = "both_minor", False  # 按原决策继续；异议就地封闭但强制留痕
    else:
        outcome, escalate = "divergent", True  # 分歧 → 交 Owner 裁决

    compared_at = _now()
    _save_json(
        _comparison_path(obj_id),
        {
            "schema": SCHEMA,
            "obj_id": obj_id,
            "outcome": outcome,
            "escalate": escalate,
            "compared_at": compared_at,
            "mentor_verdict": verdicts["mentor"]["verdict"],
            "controller_verdict": verdicts["controller"]["verdict"],
            "mentor_submitted_at": verdicts["mentor"].get("submitted_at"),
            "controller_submitted_at": verdicts["controller"].get("submitted_at"),
            "basis": "§8.5 共用清单（命中任一即为重大）；比对由确定性程序在双方均提交后执行",
        },
    )

    return {"outcome": outcome, "escalate": escalate}
