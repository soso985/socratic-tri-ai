"""§8.5 双判定：**封存 → 揭示 → 开封比对**（commit → reveal）—— task-8 · Owner 直接指令；封 G-9。

问题（G-9）
-----------
原先 ``submit_verdict`` **直接把明文落盘**，于是 §8.5 的「双方独立判定、互不可见」
**只靠阅读时序**维持：只要有人在第二方落盘前 ``cat`` 一下，就全看到了。
机制上没有拦住任何东西。

本版的做法：**封存期内磁盘上根本没有明文，只存承诺（哈希）**。
「监管读不到导师的明文」于是成为**物理事实**（那份数据在磁盘上不存在），而不是纪律。

协议（纯 ``hashlib``，**不用密钥、不做加密**）
----------------------------------------------
1. **封存** :func:`seal_verdict` —— 随机 ``salt``，只写
   ``commitment = sha256(salt + canonical_json(verdict))``；
   **不写明文、不写密文**。
2. **揭示** :func:`reveal_verdict` —— 双份封存都到位后，各自补交自己的明文；
   程序用承诺校验，不符即抛 :class:`CommitmentMismatch` 且**不落任何明文**。
3. **开封** —— **双份揭示都通过后**，才把明文写入 ``<obj_id>.<role>.json``（``state="OPENED"``），
   此后 :func:`read_verdict` 才可读、:func:`compare` 才可比对。

> 为什么不做「加密存盘」：加密只是混淆——密钥若也在工作区里，谁都能解，
> 「靠纪律」的问题原样保留，只是把 ``cat`` 换成「写三行解密代码」。

落盘
----
* 封存（封存期内**唯一**存在的文件）：``agent_state/verdicts/<obj_id>.<role>.sealed.json``
  —— 只含 ``{obj_id, role, commitment, salt, sealed_at, state}``
* 开封后的明文：``agent_state/verdicts/<obj_id>.<role>.json``（``state="OPENED"``）
* 比对留档：``agent_state/verdicts/<obj_id>.comparison.json``

``state`` 生命周期：``SEALED`` →（揭示校验通过）``REVEALED`` →（双份齐备开封）``OPENED``。

已知边界（务必知悉）
--------------------
* 承诺用**加盐哈希**：``salt`` 随封存文件落盘（契约规定字段）。因此第三方可以
  **验证一个猜测**，但无法从承诺反演出明文——因为 ``scope`` 是自由文本，穷举不可行。
  若要求「连猜测验证也不可行」，需把 salt 移出磁盘（改接口），属后续变更。
* 「双份揭示」目前在**同一进程内**完成开封：第一份揭示的明文只暂存在内存
  （磁盘上不得出现明文，这是本任务的核心约束）。进程若在其中退出，
  该方**重新调用一次** :func:`reveal_verdict`（幂等）即可继续；持久化恢复属 P4。
* 本程序只保证**封存与比对阶段不泄露**，无法阻止两个模型在**生成阶段**因同源而趋同——
  后者是 G-8（环境限制），非本程序可解。
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone

from . import data_root

__all__ = [
    "seal_verdict",
    "reveal_verdict",
    "read_verdict",
    "compare",
    "submit_verdict",
    "commitment_of",
    "canonical_verdict",
    "IncompleteJudgment",
    "SealedVerdict",
    "CommitmentMismatch",
    "AlreadySealed",
    "ROLES",
    "SCHEMA",
    "STATE_SEALED",
    "STATE_REVEALED",
    "STATE_OPENED",
]

#: 判定主体（§8.5：Mentor 与 Controller 各判一次）
ROLES = ("mentor", "controller")

#: 记录结构标识（任务内 schema，非 DEF-06）
SCHEMA = "task-internal/v1"

#: 封存文件里 ``state`` 的取值（生命周期）
STATE_SEALED = "SEALED"        # 已封存，仅承诺在盘
STATE_REVEALED = "REVEALED"    # 本方揭示已校验通过；明文仍未落盘
STATE_OPENED = "OPENED"        # 双份齐备，明文已落盘

_VERDICTS_DIR = ("agent_state", "verdicts")

#: 封存期内的内存暂存：``(数据根, obj_id, role) -> 已验证的明文``。
#: ⚠ 明文**绝不**写盘（这是本任务的核心约束），故只能暂存在内存里等第二份揭示。
#: 键里带**数据根**：同一 obj_id 若出现在不同数据根（如隔离测试），暂存互不串味。
_STAGING: dict = {}


def _staging_key(obj_id: str, role: str):
    return (str(data_root()), obj_id, role)


# ---------------------------------------------------------------- 异常


class IncompleteJudgment(Exception):
    """双判定未齐备即请求比对（**保留为基类**）。

    v2 验收项 5 曾以该类型判定「单方判定时 compare 拒绝执行」。
    task-8 起语义统一为「封存未开封」，抛出的是 :class:`SealedVerdict`；
    它继承本类，因此旧的 ``except IncompleteJudgment`` 断言**仍然成立**。
    """


class SealedVerdict(IncompleteJudgment):
    """封存未开封：拒绝读取明文、拒绝比对（G-9 的机制落点）。"""


class CommitmentMismatch(ValueError):
    """揭示内容与封存承诺不符（``sha256(salt + canonical(verdict))`` 对不上）。"""


class AlreadySealed(RuntimeError):
    """同一 ``(obj_id, role)`` 重复封存——封存不可覆盖，避免开封后偷换结论。"""


# ---------------------------------------------------------------- 内部工具


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _verdicts_dir():
    return data_root().joinpath(*_VERDICTS_DIR)


def _sealed_path(obj_id: str, role: str):
    return _verdicts_dir() / f"{obj_id}.{role}.sealed.json"


def _opened_path(obj_id: str, role: str):
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


def _validate_role(role) -> str:
    if role not in ROLES:
        raise ValueError(f"role 必须是 {list(ROLES)} 之一，收到 {role!r}")
    return role


def _validate_obj_id(obj_id) -> str:
    if not isinstance(obj_id, str) or not obj_id.strip():
        raise ValueError(f"obj_id 必填且必须是非空字符串，收到 {obj_id!r}")
    return obj_id.strip()


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


def canonical_verdict(verdict: dict) -> str:
    """判决的规范化序列化（键排序、无多余空白），保证承诺可复现。"""
    return json.dumps(verdict, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def commitment_of(salt: str, verdict: dict) -> str:
    """计算承诺：``sha256(salt || canonical_json(verdict))``。

    承诺**单向**：只有持有 ``salt`` 与完整 ``verdict`` 才能复算出它。
    """
    if not isinstance(salt, str) or not salt:
        raise ValueError("salt 必填且必须是非空字符串")
    clean = _validate_verdict(verdict)
    preimage = f"{salt}{canonical_verdict(clean)}"
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()


def _is_opened(obj_id: str) -> bool:
    """双份明文都已开封落盘？"""
    for role in ROLES:
        path = _opened_path(obj_id, role)
        if not path.exists():
            return False
        try:
            if _load_json(path).get("state") != STATE_OPENED:
                return False
        except (OSError, ValueError):
            return False
    return True


def _try_open(obj_id: str) -> bool:
    """双份封存齐备、且两份明文都已在内存中验证通过 → 落明文开封。"""
    if any(not _sealed_path(obj_id, role).exists() for role in ROLES):
        return False
    if any(_staging_key(obj_id, role) not in _STAGING for role in ROLES):
        return False

    opened_at = _now()
    for role in ROLES:
        _save_json(
            _opened_path(obj_id, role),
            {
                "schema": SCHEMA,
                "obj_id": obj_id,
                "role": role,
                "verdict": _STAGING[_staging_key(obj_id, role)],
                "state": STATE_OPENED,
                "opened_at": opened_at,
            },
        )
        sealed = _load_json(_sealed_path(obj_id, role))
        sealed["state"] = STATE_OPENED
        _save_json(_sealed_path(obj_id, role), sealed)
        _STAGING.pop(_staging_key(obj_id, role), None)
    return True


# ---------------------------------------------------------------- 对外接口


def seal_verdict(role: str, obj_id: str, verdict: dict) -> dict:
    """**封存**一方判定：只写承诺（哈希），**明文与密文都不落盘**。

    落盘文件 ``<obj_id>.<role>.sealed.json`` **只含**：
    ``{obj_id, role, commitment, salt, sealed_at, state}``。

    已封存的 ``(obj_id, role)`` 不允许重复封存（抛 :class:`AlreadySealed`），
    以免开封后偷换结论。
    """
    role = _validate_role(role)
    obj_id = _validate_obj_id(obj_id)
    clean = _validate_verdict(verdict)

    sealed_path = _sealed_path(obj_id, role)
    if sealed_path.exists() or _opened_path(obj_id, role).exists():
        raise AlreadySealed(
            f"{obj_id} / {role} 已封存或已开封，不得重复封存（封存不可覆盖）"
        )

    salt = secrets.token_hex(16)
    commitment = commitment_of(salt, clean)
    sealed_at = _now()
    sealed = {
        "obj_id": obj_id,
        "role": role,
        "commitment": commitment,
        "salt": salt,
        "sealed_at": sealed_at,
        "state": STATE_SEALED,
    }
    _save_json(sealed_path, sealed)
    return {
        "obj_id": obj_id,
        "role": role,
        "commitment": commitment,
        "salt": salt,
        "state": STATE_SEALED,
        "sealed_at": sealed_at,
        "sealed_path": str(sealed_path),
    }


def reveal_verdict(role: str, obj_id: str, verdict: dict) -> dict:
    """**揭示**一方判定：用承诺校验明文。

    * 未封存就揭示 → :class:`SealedVerdict`；
    * 明文与承诺不符 → :class:`CommitmentMismatch`，**不落任何明文**；
    * 校验通过后明文**仍不落盘**，只暂存内存；**双份揭示都通过**才开封落盘。

    本函数**幂等**：同一方重复揭示不会改变结果，也不会重复开封。
    """
    role = _validate_role(role)
    obj_id = _validate_obj_id(obj_id)
    clean = _validate_verdict(verdict)

    sealed_path = _sealed_path(obj_id, role)
    if not sealed_path.exists():
        raise SealedVerdict(
            f"{obj_id} / {role} 尚未封存，无法揭示；封存是揭示的前置条件。"
        )
    sealed = _load_json(sealed_path)

    if commitment_of(sealed["salt"], clean) != sealed["commitment"]:
        # 校验失败：不得留下任何明文，也不得改动封存状态
        raise CommitmentMismatch(
            f"{obj_id} / {role} 揭示的判决与封存承诺不符："
            f"sha256(salt + canonical(verdict)) != commitment（{sealed['commitment']}）。"
            "已拒绝揭示，磁盘上未写入任何明文。"
        )

    _STAGING[_staging_key(obj_id, role)] = clean          # 明文只在内存，绝不落盘
    if sealed.get("state") == STATE_SEALED:
        sealed["state"] = STATE_REVEALED
        _save_json(sealed_path, sealed)

    opened = _try_open(obj_id)
    return {
        "obj_id": obj_id,
        "role": role,
        "commitment": sealed["commitment"],
        "state": STATE_OPENED if opened else STATE_REVEALED,
        "opened": opened,
        "opened_path": str(_opened_path(obj_id, role)) if opened else None,
    }


def read_verdict(role: str, obj_id: str) -> dict:
    """读取某一方的明文判定 —— **仅双份揭示都完成（已开封）后**才可读。

    任一 seal 缺失、或双 reveal 未完成 → :class:`SealedVerdict`（拒绝读取）。
    """
    role = _validate_role(role)
    obj_id = _validate_obj_id(obj_id)

    missing = [r for r in ROLES if not _sealed_path(obj_id, r).exists()]
    if missing:
        raise SealedVerdict(
            f"{obj_id} 尚未完成双份封存（缺少：{', '.join(missing)}）→ 拒绝读取明文。"
        )
    if not _is_opened(obj_id):
        raise SealedVerdict(
            f"{obj_id} 的双份揭示尚未全部完成 → 拒绝读取明文（封存期内磁盘上没有明文）。"
        )

    record = _load_json(_opened_path(obj_id, role))
    return {
        "obj_id": obj_id,
        "role": role,
        "verdict": record["verdict"],
        "state": STATE_OPENED,
        "opened_at": record.get("opened_at"),
    }


def compare(obj_id: str) -> dict:
    """**双份揭示都完成后**才比对，返回 ``{"outcome": ..., "escalate": ...}``。

    未开封 → :class:`SealedVerdict`。
    ``both_major`` / ``divergent`` → ``escalate=True``；``both_minor`` → ``escalate=False``。
    比对结果与双方原始结论一并留档。
    """
    obj_id = _validate_obj_id(obj_id)

    if not _is_opened(obj_id):
        raise SealedVerdict(
            f"{obj_id} 未完成「双份封存 + 双份揭示」→ 拒绝比对。"
            "§8.5 要求双方独立判定、互不可见；封存未开封时任何比对都会泄露一方结论。"
        )

    verdicts = {role: _load_json(_opened_path(obj_id, role)) for role in ROLES}
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
            "mentor_opened_at": verdicts["mentor"].get("opened_at"),
            "controller_opened_at": verdicts["controller"].get("opened_at"),
            "basis": "§8.5 共用清单（命中任一即为重大）；双方封存并揭示齐备后由确定性程序比对",
        },
    )

    return {"outcome": outcome, "escalate": escalate}


def submit_verdict(role: str, obj_id: str, verdict: dict) -> dict:
    """兼容别名 —— 语义已由「直接落明文」改为**封存**（task-8）。

    保留旧名只为不破坏既有调用点；它等价于 :func:`seal_verdict`。
    """
    return seal_verdict(role, obj_id, verdict)
