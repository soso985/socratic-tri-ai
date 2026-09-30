"""状态护栏：**暂停免费，解暂停必须凭「用户已批准」记录** —— task-9 · Owner 直接指令；封 RES-01 实质。

问题（RES-01）
-------------
V1 的写锁把信任集中到 ``agent_state/task_state.json`` 这一个文件上：
**谁能改它，谁就能把状态改成 RUNNING 自行解锁**。锁本身是对的，但权威文件没有任何护栏。

本模块给出的**不对称**（Owner 的原始要求）
------------------------------------------
* **暂停永远免费** —— 任何方向进入暂停类状态都不需要凭证（停下来是安全的）；
* **解暂停必须留下「用户已批准」的记录** —— 没有那条记录，程序**没有任何路径**
  能把状态从暂停改成可执行/RUNNING，且**绝不写盘**。

批准记录是**一次性**的（``consumed: true``），用后即焚，防止一张凭证反复解锁。

⚠ 一处必须点明的判定（比任务里的收紧更进一步，理由附后）
------------------------------------------------------
任务原文把批准要求收紧为「**当前 ∈ PAUSED 且 to_state ∈ EXECUTABLE**」。
本实现把触发条件放宽为「**任何离开暂停类的跃迁**」（``current ∈ PAUSED_STATES``），
因为按前者存在**多跳绕过**（已实测，见演示与报告）：

    BLOCKED → AUDITING → READY → RUNNING

这三跳在 §10.1 跃迁表下**全部合法**，而只有第一跳离开暂停；第一跳的落点 ``AUDITING``
不属于可执行类，于是**全程都不需要批准记录**就到达了 ``RUNNING``——
Owner 明确要求「不允许程序自己把状态从暂停改回 RUNNING 或可执行」，该路径会击穿它。
放宽后：**任何解暂停动作**都要凭证（仍然只有解暂停要，正常运行如 ``READY→RUNNING``
不需要），既堵住多跳，也不违背 §12.3「减少打扰」。
该判定已在工作报告中显式登记，等待 Mentor/Owner 裁定。

与既有模块的关系
----------------
本模块**不修改**任何既有模块（``write_lock`` / ``objection`` / ``dual_judge`` / ``gate`` / ``audit_log``），
只在运行时：改写 ``task_state.json``、写 ``agent_state/approvals/``、向 ``audit_log`` 追加事件。
跃迁表校验**复用** :mod:`control.gate` 的公开常量（``LEGAL_TRANSITIONS`` / ``BLOCKED_ENTRY_STATES``），
不重复维护第二份表。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import audit_log, data_root, gate

__all__ = [
    "set_paused",
    "record_user_approval",
    "request_transition",
    "current_status",
    "read_approval",
    "approval_path",
    "PAUSED_STATES",
    "EXECUTABLE_STATES",
    "DEFAULT_PAUSED_STATE",
    "KNOWN_STATES",
    "USER_APPROVER",
    "StateGuardError",
    "TransitionDenied",
    "ApprovalRequired",
    "InvalidApproval",
    "ApprovalNotFound",
    "ApprovalMismatch",
    "ApprovalConsumed",
    "IllegalTransition",
    "CredentialNotAllowed",
    "CredentialNotFound",
    "CredentialConsumed",
    "CredentialMismatch",
]

#: 暂停类状态（任务描述给定）
PAUSED_STATES = frozenset({"等用户裁决", "AWAITING_USER_RULING", "BLOCKED", "ESCALATED"})

#: 可执行类状态（任务描述给定）
EXECUTABLE_STATES = frozenset({"READY", "RUNNING"})

#: 默认的暂停态
DEFAULT_PAUSED_STATE = "等用户裁决"

#: 已知状态集合（§10.1 十态 + 本任务新增的两个暂停态）
KNOWN_STATES = frozenset(
    {
        "DRAFT", "AUDITING", "READY", "RUNNING", "SUBMITTED", "ACCEPTED",
        "REWORK", "BLOCKED", "CHANGE_PENDING", "ESCALATED",
        "等用户裁决", "AWAITING_USER_RULING",
    }
)

#: 唯一被认可的批准人 —— **恰为** ``"Owner"``（大小写敏感）
USER_APPROVER = "Owner"

_APPROVALS_DIR = ("agent_state", "approvals")

#: 审计事件里记录的操作名
_AUDIT_KIND = "state_guard"


# ---------------------------------------------------------------- 异常


class StateGuardError(Exception):
    """本模块所有错误的基类。"""


class TransitionDenied(StateGuardError, PermissionError):
    """状态跃迁被拒绝（一律**不写盘**）。"""


class ApprovalRequired(TransitionDenied):
    """从暂停类状态解暂停，但没有提供用户批准记录。"""


class InvalidApproval(TransitionDenied):
    """批准记录不合法（如 ``approved_by`` 不是 ``"Owner"``）。"""


class ApprovalNotFound(TransitionDenied):
    """引用的批准记录不存在。"""


class ApprovalMismatch(TransitionDenied):
    """批准记录与本次跃迁不匹配（task_id 不符，或记录里的批准人不是 Owner）。"""


class ApprovalConsumed(TransitionDenied):
    """批准记录已被消费——一次性凭证不得重放。"""


class IllegalTransition(TransitionDenied):
    """跃迁不在 §10.1 跃迁表内（非解暂停路径）。"""


class CredentialNotAllowed(TransitionDenied):
    """规则凭证**只能开 BLOCKED 这一扇门**：其它暂停态（ESCALATED / CHANGE_PENDING / 等用户裁决）一律拒绝。"""


class CredentialNotFound(TransitionDenied):
    """按 ``cred_id`` 在 ``agent_state/rule_credentials/`` 下**唯一**匹配失败（0 个或多个）——不猜。"""


class CredentialConsumed(TransitionDenied):
    """该规则凭证已被消费（一次性，用一次就作废）。"""


class CredentialMismatch(TransitionDenied):
    """凭证与本次跃迁不匹配（``cred_id`` / ``task_id`` / ``issued_by`` / ``basis`` / ``outcome``）。"""


# ---------------------------------------------------------------- 内部工具


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y%m%d")


def _task_state_path():
    return data_root() / "agent_state" / "task_state.json"


def _approvals_dir():
    return data_root().joinpath(*_APPROVALS_DIR)


def approval_path(task_id: str, approval_id: str):
    """批准记录的落盘路径：``agent_state/approvals/<task_id>.<approval_id>.json``。"""
    return _approvals_dir() / f"{task_id}.{approval_id}.json"


def _load_json(path, default):
    if not path.exists():
        return json.loads(json.dumps(default))
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _require_task_id(task_id) -> str:
    if not isinstance(task_id, str) or not task_id.strip():
        raise StateGuardError(f"task_id 必填且必须是非空字符串，收到 {task_id!r}")
    return task_id.strip()


def _load_state_file() -> dict:
    return _load_json(_task_state_path(), {"schema": "task-internal/v1", "tasks": {}})


def current_status(task_id):
    """读权威文件里该任务的当前状态；任务或文件缺失时返回 ``None``。"""
    task_id = _require_task_id(task_id)
    try:
        state = _load_state_file()
    except (OSError, ValueError):
        return None
    record = (state.get("tasks") or {}).get(task_id)
    if not isinstance(record, dict):
        return None
    status = record.get("status")
    return status if isinstance(status, str) and status.strip() else None


def _write_status(task_id: str, new_status: str, *, reason: str, extra=None) -> dict:
    """改写任务状态（保留记录里的其它字段），并落审计。"""
    path = _task_state_path()
    state = _load_state_file()
    tasks = state.setdefault("tasks", {})
    record = dict(tasks.get(task_id) or {})
    previous = record.get("status")
    record["status"] = new_status
    record["previous_status"] = previous
    record["updated_at"] = _now()
    record["state_reason"] = reason
    if extra:
        record.update(extra)
    tasks[task_id] = record
    state["schema"] = state.get("schema", "task-internal/v1")
    state["updated_at"] = record["updated_at"]
    _save_json(path, state)
    audit_log.append(
        {
            "kind": _AUDIT_KIND,
            "action": "status_written",
            "task_id": task_id,
            "source": previous,
            "target": new_status,
            "reason": reason,
            "basis": "task-9（Owner 直接指令）· 暂停免费 / 解暂停须凭用户批准记录",
        }
    )
    return {"task_id": task_id, "status": new_status, "previous_status": previous,
            "state_path": str(path), "updated_at": record["updated_at"]}


def _audit_denial(task_id, action, to_state, reason, **extra) -> None:
    """拒绝也要留痕（§10.2-36 精神）；**只写审计日志，绝不碰 task_state.json**。"""
    try:
        audit_log.append(
            {
                "kind": _AUDIT_KIND,
                "action": action,
                "task_id": task_id,
                "target": to_state,
                "reason": reason,
                "wrote_state": False,
                "basis": "task-9（Owner 直接指令）· 解暂停必须凭用户批准记录",
                **extra,
            }
        )
    except Exception:  # noqa: BLE001 - 留痕失败不得改变"拒绝"这一结论
        pass


def _table_allows(source: str, target: str) -> bool:
    """按 §10.1 跃迁表（含 TASK-001 v2 与勘误 01 的追加项）校验。

    复用 :mod:`control.gate` 的公开常量，避免维护第二份表：
    ``BLOCKED → 进入 BLOCKED 前的状态`` 的合法目标集合即 ``gate.BLOCKED_ENTRY_STATES``
    （依据 V1.4-ERR-01）。
    """
    if (source, target) in gate.LEGAL_TRANSITIONS:
        return True
    if source == "BLOCKED" and target in gate.BLOCKED_ENTRY_STATES:
        return True
    return False


def _next_approval_id() -> str:
    """生成批准记录 ID（``APR-YYYYMMDD-NNN``，当日自增；不接受调用方传入）。"""
    prefix = f"APR-{_today()}-"
    highest = 0
    directory = _approvals_dir()
    if directory.is_dir():
        for path in directory.glob("*.json"):
            stem = path.stem  # <task_id>.<approval_id>
            for part in stem.split("."):
                if part.startswith(prefix) and part[len(prefix):].isdigit():
                    highest = max(highest, int(part[len(prefix):]))
    return f"{prefix}{highest + 1:03d}"


def _consume_approval(task_id: str, approval_id) -> dict:
    """校验并**一次性消费**批准记录；任何不合法情形都不写盘。"""
    if not approval_id:
        raise ApprovalRequired(
            f"从暂停类状态解暂停必须有用户批准记录："
            f"当前任务 {task_id} 处于暂停类，未提供 approval_id → 拒绝，且不写盘。"
        )
    if not isinstance(approval_id, str) or not approval_id.strip():
        raise ApprovalRequired(f"approval_id 必须是非空字符串，收到 {approval_id!r}")
    approval_id = approval_id.strip()

    path = approval_path(task_id, approval_id)
    if not path.exists():
        raise ApprovalNotFound(
            f"批准记录不存在：{path}（不得凭空解暂停）→ 拒绝，且不写盘。"
        )
    record = _load_json(path, {})
    if record.get("task_id") != task_id:
        raise ApprovalMismatch(
            f"批准记录的 task_id={record.get('task_id')!r} 与本次 {task_id!r} 不符 → 拒绝，且不写盘。"
        )
    if record.get("approved_by") != USER_APPROVER:
        raise ApprovalMismatch(
            f"批准记录的 approved_by={record.get('approved_by')!r} 不是 {USER_APPROVER!r} → 拒绝，且不写盘。"
        )
    if record.get("consumed") is True:
        raise ApprovalConsumed(
            f"批准记录 {approval_id} 已被消费（consumed:true，一次性凭证）→ 拒绝重放，且不写盘。"
        )

    record["consumed"] = True
    record["consumed_at"] = _now()
    _save_json(path, record)   # 先消费（fail-closed）：即便随后的状态写入失败，凭证也不会被重复使用
    return record


def _consume_rule_credential(task_id: str, rule_credential_id, status) -> dict:
    """校验并**一次性消费**规则凭证（GUARD-01 消费端）。

    **规则凭证只能开 ``BLOCKED`` 这一扇门**：下列五条**全部**满足才放行，任一不满足即拒绝，
    且**绝不改动状态文件**（也绝不改动凭证文件）。

    ① 当前状态**恰好**是 ``BLOCKED``（``ESCALATED`` / ``CHANGE_PENDING`` / 「等用户裁决」→ 一律拒绝）；
    ② 在 ``agent_state/rule_credentials/`` 下按 ``*.<cred_id>.json`` **唯一**匹配（0 个或多个都拒绝，不猜）；
    ③ 确实是 :func:`control.dual_judge.compare` 签发的（``issued_by == "dual_judge.compare"``，
       且 ``basis`` 表明 §8.5 both_minor、``outcome == "both_minor"``）；
    ④ ``consumed`` 为假（未消费）；
    ⑤ 凭证的 ``task_id`` 与本次要改的任务一致。

    ⚠ **本函数不解决"凭证文件可被直接伪造"**——那是 Owner 明示排除的已知边界（见 task-11 硬约束）。
    """
    # ① 只有 BLOCKED 这扇门
    if status != "BLOCKED":
        raise CredentialNotAllowed(
            f"规则凭证只能用于离开 BLOCKED，当前状态是 {status!r} → 拒绝，且不改状态文件。"
        )

    # 参数基本校验（顺带挡住 glob 元字符/路径穿越）
    if not isinstance(rule_credential_id, str) or not rule_credential_id.strip():
        raise CredentialNotFound(
            f"rule_credential_id 必须是非空字符串，收到 {rule_credential_id!r} → 拒绝。"
        )
    cred_id = rule_credential_id.strip()
    if any(ch in cred_id for ch in "*/\\?[]") or ".." in cred_id:
        raise CredentialNotFound(f"rule_credential_id 含非法字符：{cred_id!r} → 拒绝。")

    # ② 唯一匹配（0 个或多个 → 拒绝）
    directory = data_root() / "agent_state" / "rule_credentials"
    matches = sorted(directory.glob(f"*.{cred_id}.json")) if directory.is_dir() else []
    if len(matches) != 1:
        raise CredentialNotFound(
            f"规则凭证按 cred_id={cred_id!r} 唯一匹配失败：命中 {len(matches)} 个 → "
            "拒绝（fail-closed，不猜）→ 且不改状态文件。"
        )
    path = matches[0]
    record = _load_json(path, {})
    if not isinstance(record, dict):
        raise CredentialMismatch(f"规则凭证内容不是 JSON 对象：{path} → 拒绝。")

    # ③ 确实由 compare() 签发
    if record.get("cred_id") != cred_id:
        raise CredentialMismatch(
            f"凭证记录的 cred_id={record.get('cred_id')!r} 与请求的 {cred_id!r} 不符 → 拒绝。"
        )
    if record.get("issued_by") != "dual_judge.compare":
        raise CredentialMismatch(
            f"凭证 issued_by={record.get('issued_by')!r} 不是 'dual_judge.compare' → 拒绝。"
        )
    basis = str(record.get("basis", ""))
    if "§8.5" not in basis or "both_minor" not in basis or record.get("outcome") != "both_minor":
        raise CredentialMismatch(
            f"凭证未表明「§8.5 both_minor」：basis={basis!r}、outcome={record.get('outcome')!r} → 拒绝。"
        )

    # ④ 未消费
    if record.get("consumed") is True:
        raise CredentialConsumed(
            f"规则凭证 {cred_id} 已被消费（一次性，用一次就作废）→ 拒绝重放，且不改状态文件。"
        )

    # ⑤ task_id 一致
    if record.get("task_id") != task_id:
        raise CredentialMismatch(
            f"凭证 task_id={record.get('task_id')!r} 与目标任务 {task_id!r} 不一致 → 拒绝，且不改状态文件。"
        )

    record["consumed"] = True
    record["consumed_at"] = _now()
    record["consumed_by"] = "state_guard.request_transition"
    _save_json(path, record)   # 先消费（fail-closed）
    return record


# ---------------------------------------------------------------- 对外接口


def set_paused(task_id, reason) -> dict:
    """把任务置为暂停类状态（默认 :data:`DEFAULT_PAUSED_STATE` = 「等用户裁决」）。

    **暂停永远免费**：本方向不需要任何批准记录——停下来是安全的。
    """
    task_id = _require_task_id(task_id)
    if not isinstance(reason, str) or not reason.strip():
        raise StateGuardError("reason 必填且必须是非空字符串（暂停也必须说明原因）")
    result = _write_status(task_id, DEFAULT_PAUSED_STATE, reason=reason.strip(),
                           extra={"paused_at": _now()})
    result["paused"] = True
    return result


def record_user_approval(task_id, scope, approved_by, statement) -> dict:
    """登记一条**用户批准**记录（解暂停的唯一凭证）。

    ``approved_by`` **必须恰为** ``"Owner"``，否则抛 :class:`InvalidApproval`，**且不落盘**。

    落盘 ``agent_state/approvals/<task_id>.<approval_id>.json``，字段：
    ``{approval_id, task_id, approved_by, approved_at, scope, statement, consumed:false}``。

    ⚠ **本函数只校验身份声明，不做密码学认证**：任何能写工作区的程序都能登记一条
    ``approved_by="Owner"`` 的记录。它把「自行解锁」从"随手改状态"抬高为"必须留下一条
    可追溯、一次性、具名的记录"，但不等于 Owner 的真实签名。若要真正防伪造，
    需要工作区之外的密钥/签名通道（属后续变更，见工作报告 R 项）。
    """
    task_id = _require_task_id(task_id)
    if approved_by != USER_APPROVER:
        # 先校验、后落盘：非法批准人不得留下任何文件
        raise InvalidApproval(
            f"approved_by 必须恰为 {USER_APPROVER!r}，收到 {approved_by!r} → 拒绝，且不落盘。"
        )
    for name, value in (("scope", scope), ("statement", statement)):
        if not isinstance(value, str) or not value.strip():
            raise InvalidApproval(f"{name} 必填且必须是非空字符串，收到 {value!r}")

    approval_id = _next_approval_id()
    approved_at = _now()
    record = {
        "approval_id": approval_id,
        "task_id": task_id,
        "approved_by": USER_APPROVER,
        "approved_at": approved_at,
        "scope": scope.strip(),
        "statement": statement.strip(),
        "consumed": False,
    }
    path = approval_path(task_id, approval_id)
    _save_json(path, record)
    audit_log.append(
        {
            "kind": _AUDIT_KIND,
            "action": "user_approval_recorded",
            "task_id": task_id,
            "approval_id": approval_id,
            "approved_by": USER_APPROVER,
            "scope": record["scope"],
            "basis": "task-9（Owner 直接指令）· 解暂停的唯一凭证",
        }
    )
    return dict(record, approval_path=str(path))


def read_approval(task_id, approval_id) -> dict:
    """读一条批准记录；不存在则抛 :class:`ApprovalNotFound`。"""
    task_id = _require_task_id(task_id)
    path = approval_path(task_id, approval_id)
    if not path.exists():
        raise ApprovalNotFound(f"批准记录不存在：{path}")
    return _load_json(path, {})


def request_transition(task_id, to_state, *, approval_id=None, rule_credential_id=None) -> dict:
    """请求把任务状态切到 ``to_state``。

    规则（核心不变量）
    ------------------
    * **当前状态属于暂停类（或无法判定）→ 必须先拿到一种授权**，否则抛
      :class:`ApprovalRequired`，**且一个字节都不写**（``task_state.json`` 不变）。两种授权：
      1. ``approval_id``：Owner 批准记录（既有路径，行为不变）；
      2. ``rule_credential_id``：**规则凭证**（GUARD-01 消费端，task-11）——
         **只能开 ``BLOCKED`` 这一扇门**，且五条全满足才放行：
         ①当前状态恰好 ``BLOCKED``；②按 ``cred_id`` 在 ``agent_state/rule_credentials/``
         下唯一匹配；③确为 :func:`control.dual_judge.compare` 签发（``issued_by`` +
         ``basis`` 表明 §8.5 both_minor）；④``consumed`` 为假；⑤``task_id`` 一致。
         任一不满足 → 抛 :class:`CredentialNotAllowed` / :class:`CredentialNotFound` /
         :class:`CredentialConsumed` / :class:`CredentialMismatch`，**绝不改状态文件**。
         用一次即作废（落盘 ``consumed:true``）。
         两者同时给出时**以 ``approval_id`` 为准**（既有路径优先）。
    * 非暂停状态的跃迁：按 §10.1 跃迁表校验（复用 :mod:`control.gate` 的常量），
      合法则执行，**不追加批准要求**（正常运行不该被 Owner 打断）。
    * 任何一次**状态改写**都会落审计日志（复用 :func:`audit_log.append`）。

    返回值含 ``approval_required`` / ``rule_credential_consumed`` 便于调用方与测试断言。
    """
    task_id = _require_task_id(task_id)
    if not isinstance(to_state, str) or to_state not in KNOWN_STATES:
        raise IllegalTransition(f"to_state 必须是已知状态之一，收到 {to_state!r}")

    status = current_status(task_id)
    # 无法判定当前状态时按"暂停"处理（fail-closed）：绝不因读不到状态就放行
    leaving_pause = (status is None) or (status in PAUSED_STATES)

    # ---- 路径 1：规则凭证（task-11 · GUARD-01 消费端）----
    # 只要调用方给出凭证，就先做「**只能用来离开 BLOCKED**」的判定：
    # 当前状态不是 BLOCKED（含 ESCALATED / CHANGE_PENDING / 「等用户裁决」等）
    # 一律抛 CredentialNotAllowed —— **不被静默忽略、也不改状态文件**。
    # 注意：这里刻意不放在下面的 `leaving_pause` 分支内，因为 CHANGE_PENDING
    # 不在 PAUSED_STATES 中，否则凭证会被"绕过"（既不消费也不拒绝）。
    if approval_id is None and rule_credential_id is not None:
        try:
            credential = _consume_rule_credential(task_id, rule_credential_id, status)
        except TransitionDenied as exc:
            _audit_denial(
                task_id,
                "transition_denied",
                to_state,
                f"{type(exc).__name__}: {exc}",
                source=status,
                rule_credential_id=rule_credential_id,
            )
            raise
        result = _write_status(
            task_id,
            to_state,
            reason=f"规则凭证（{credential['cred_id']}，§8.5 both_minor）解暂停",
            extra={"rule_credential_id": credential["cred_id"],
                   "credential_basis": credential.get("basis")},
        )
        return dict(result, approval_required=False, approval_id=None,
                    rule_credential_id=credential["cred_id"],
                    rule_credential_consumed=True)

    if leaving_pause:
        # ---- 路径 2：Owner 批准记录（既有路径，行为不变）----
        try:
            approval = _consume_approval(task_id, approval_id)
        except TransitionDenied as exc:
            _audit_denial(
                task_id,
                "transition_denied",
                to_state,
                f"{type(exc).__name__}: {exc}",
                source=status,
                approval_id=approval_id,
            )
            raise
        result = _write_status(
            task_id,
            to_state,
            reason=f"用户批准（{approval['approval_id']}）解暂停",
            extra={"approved_by": approval["approved_by"], "approval_id": approval["approval_id"],
                   "approval_scope": approval.get("scope")},
        )
        return dict(result, approval_required=True, approval_id=approval["approval_id"],
                    rule_credential_id=None, rule_credential_consumed=False)

    if not _table_allows(status, to_state):
        exc = IllegalTransition(
            f"跃迁 {status} → {to_state} 不在 §10.1 跃迁表内（非解暂停路径）→ 拒绝，且不写盘。"
        )
        _audit_denial(task_id, "transition_denied", to_state, f"{type(exc).__name__}: {exc}",
                      source=status, approval_id=approval_id)
        raise exc

    result = _write_status(task_id, to_state, reason="常规跃迁（非解暂停，无需批准）")
    return dict(result, approval_required=False, approval_id=None,
                rule_credential_id=None, rule_credential_consumed=False)
