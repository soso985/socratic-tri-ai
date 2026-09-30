"""真实写文件锁 —— **状态只认权威文件**（task-6 · Owner 直接指令；封 R-1）。

背景
----
task-5 的 ``locked_write`` 把 ``task_state`` 交给**调用方**传入，因此调用方可以撒谎：
传 ``{"status": "RUNNING"}`` 就能放行（Executor 主动登记的风险 **R-1**）。
本版按 Owner 指令把状态来源改为**唯一权威文件**：

    control.data_root() / "agent_state" / "task_state.json"   →   tasks.<task_id>.status

与 :func:`control.objection._task_state_path` **同一约定**（同一份文件、同一套键名）。

两条必须区分清楚的接口语义
--------------------------
1. **``task_id`` 是「选择器」，不是「状态断言」。**
   权威文件是多任务结构 ``{"tasks": {"TASK-001": {...}, ...}}``，不指定读哪一条就无从判定，
   所以必须传入 ``task_id``。它只决定**读哪一条记录**；它**不能**提供状态，
   也**不能**把某条记录"声明"成 RUNNING。传 ``task_id`` 与"调用方传入状态"是两件事。
2. **调用方传入的状态参数一律忽略。**
   形参 ``task_state`` 为兼容既有调用点而保留，但**完全不参与状态判定**——
   传 ``{"status": "RUNNING"}`` / ``{"state": "RUNNING"}`` / ``{"current_state": "RUNNING"}``
   或任何其它写法都不会改变结果，锁只读权威文件。
   ⚠ **一个被静默忽略的参数是陷阱**：有人传了 ``RUNNING`` 却仍被拒，会误以为锁坏了。
   因此这一点同时写在**本模块 docstring**、**:func:`locked_write` 的 docstring**
   和**拒绝消息**里。

失败即拒绝（fail-closed）
-------------------------
下列情形**全部拒绝写入**，绝不"放行以求稳"：权威文件不存在；文件存在但读不出 / JSON 损坏 /
编码错误 / 权限错误；``tasks`` 结构缺失；``<task_id>`` 不在其中；``status`` 缺失 / 非字符串 / 空串；
``status != "RUNNING"``；``task_id`` 本身非法。

拒绝时：抛 :class:`WriteDenied`；**目标文件一个字节都不写**（拒绝路径不 ``open()``、不 ``mkdir()``、
不建临时文件，在任何文件系统操作之前返回）；并向审计日志追加一条拒绝记录。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from . import audit_log, data_root

__all__ = [
    "locked_write",
    "WriteDenied",
    "StateSourceError",
    "REQUIRED_STATE",
    "TASK_STATE_RELATIVE",
    "read_authoritative_status",
    "task_state_path",
]

#: 允许写入所需的唯一状态
REQUIRED_STATE = "RUNNING"

#: 权威状态文件相对数据根的路径（与 ``objection._task_state_path()`` 同一约定）
TASK_STATE_RELATIVE = ("agent_state", "task_state.json")

#: 调用方可能用来"假装"传状态的键名（一律忽略；此处仅用于在消息/审计里点名）
CALLER_STATE_KEYS = ("status", "state", "current_state")


class StateSourceError(Exception):
    """权威状态不可用（文件缺失 / 读不出 / 结构不符）。

    由 :func:`read_authoritative_status` 抛出；:func:`locked_write` 一律将其转为拒绝。
    """


class WriteDenied(PermissionError):
    """状态非 ``RUNNING`` 或权威状态不可用时的写入拒绝。

    属性：
        path            被拒绝的目标路径（原样，未做任何文件系统操作）
        task_id         本次用于**选择记录**的 task_id（选择器，非状态断言）
        state           权威文件里读到的真实状态；不可用时为 ``None``
        state_source    权威状态文件的路径
        required_state  允许写入所需的状态（``RUNNING``）
        audit_hash      拒绝记录在审计日志中的链式哈希；写入失败时为 ``None``
        audit_error     审计日志写入失败的原因；成功时为 ``None``
    """

    def __init__(self, message, *, path=None, task_id=None, state=None, state_source=None,
                 audit_hash=None, audit_error=None):
        super().__init__(message)
        self.path = path
        self.task_id = task_id
        self.state = state
        self.state_source = state_source
        self.required_state = REQUIRED_STATE
        self.audit_hash = audit_hash
        self.audit_error = audit_error


def task_state_path() -> Path:
    """权威状态文件的路径（唯一状态来源）。"""
    return data_root().joinpath(*TASK_STATE_RELATIVE)


def read_authoritative_status(task_id) -> str:
    """从**唯一权威文件**读取 ``tasks.<task_id>.status``。

    参数 ``task_id`` 是**选择器**（读哪一条记录），不是状态断言。

    返回读到的状态字符串；任何不可用情形抛 :class:`StateSourceError`，
    调用方必须按「拒绝」处理（fail-closed），不得放行。
    """
    if not isinstance(task_id, str) or not task_id.strip():
        raise StateSourceError(
            f"task_id 必须是用于选择任务记录的非空字符串（选择器），收到 {task_id!r}"
        )
    task_id = task_id.strip()
    path = task_state_path()

    if not path.exists():
        raise StateSourceError(f"权威状态文件不存在：{path}")

    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateSourceError(
            f"权威状态文件读取失败：{path}（{type(exc).__name__}: {exc}）"
        ) from exc

    if not isinstance(payload, dict):
        raise StateSourceError(f"权威状态文件结构不符：顶层不是 JSON 对象（{path}）")
    tasks = payload.get("tasks")
    if not isinstance(tasks, dict):
        raise StateSourceError(f"权威状态文件结构不符：缺少 tasks 对象（{path}）")
    record = tasks.get(task_id)
    if not isinstance(record, dict):
        raise StateSourceError(f"权威状态文件中没有任务 {task_id!r} 的记录（{path}）")
    status = record.get("status")
    if not isinstance(status, str) or not status.strip():
        raise StateSourceError(f"任务 {task_id!r} 的 status 缺失或非法（{path}）")
    return status.strip()


def _caller_note(task_state) -> str:
    """生成「调用方传入值已被忽略」的说明（拒绝消息与审计记录共用同一口径）。"""
    if task_state is None:
        return ("调用方本次未传入 task_state；**即便传入也会被忽略**——"
                "状态只认权威文件，传入的 status/state/current_state 一律不参与判定")
    return (f"调用方传入的 task_state={task_state!r} 已被**忽略**——"
            "状态只认权威文件，传入的 status/state/current_state 一律不参与判定")


def _record_denial(target, task_id, state, state_source, source_error, reason, task_state):
    """把「写被拒绝」写进审计日志 —— **本函数豁免于本锁**。

    ⚠ 关键设计点（Owner 指令点名要求保留）：
    拒绝记录本身写审计日志这一动作，**必须豁免于写锁**。
    否则会陷入鸡生蛋死循环——若连"记一笔拒绝"都要求权威状态为 ``RUNNING``，
    那么恰恰在**非 RUNNING**（唯一可能发生拒绝的）状态下，
    "我被拒绝了"这件事永远记不下来，锁就从"可审计的拦截"退化成"静默失败"。

    因此这里**直接调用** :func:`control.audit_log.append`，**不经过** :func:`locked_write`；
    审计日志本身是**追加式（append-only）+ 链式哈希**的，豁免范围严格限定为
    「为一次被拒绝的写入追加一条拒绝记录」，**不构成对任意路径的写入放行**。

    记录里写明：权威来源路径、读到的真实状态（或读取失败原因）、以及**调用方传入值已被忽略**。
    记录失败不吞掉：返回 ``(None, 异常对象)``，由调用方挂到 :class:`WriteDenied` 上。
    """
    try:
        event_hash = audit_log.append(
            {
                "kind": "write_lock.denied",
                "path": str(target),
                "task_id": task_id,
                "task_id_role": "选择器（读哪条记录），不是状态断言",
                "state": state,
                "state_source": str(state_source),
                "state_source_error": source_error,
                "required_state": REQUIRED_STATE,
                "caller_supplied_state_ignored": True,
                "caller_supplied_task_state": repr(task_state),
                "bytes_written": 0,
                "reason": reason,
                "basis": "RULE-01 · task-6（Owner 直接指令）· 状态只认权威文件；拒绝记录豁免于本锁",
            }
        )
        return event_hash, None
    except Exception as exc:  # noqa: BLE001 - 留痕失败必须如实上报，且不得因此放行写入
        return None, exc


def locked_write(path, content, *, task_id, task_state=None, encoding="utf-8"):
    """**状态门控的写文件**：只有权威文件里 ``tasks.<task_id>.status == "RUNNING"`` 才允许落盘。

    参数
    ----
    path       目标文件路径（``str`` 或 ``Path``）
    content    要写入的内容（``str`` 或 ``bytes``）
    task_id    **必填**。用于从权威文件 ``agent_state/task_state.json`` 中**选择读哪一条记录**。
               它是**选择器**，不是状态断言——传它不等于"声明任务状态"，
               更不能替代权威文件里的 ``status`` 字段。
    task_state **保留但被忽略**（仅为兼容既有调用点）。传任何值都不会改变判定结果；
               传 ``{"status": "RUNNING"}`` 也不会放行。
    encoding   文本编码，默认 UTF-8

    状态来源
    --------
    唯一权威文件：``control.data_root()/agent_state/task_state.json`` 的 ``tasks.<task_id>.status``。
    调用方传入的 ``status`` / ``state`` / ``current_state`` **一律忽略**。
    文件缺失、读不出、结构不符、状态缺失或非 ``RUNNING`` → **一律拒绝**（fail-closed）。

    返回
    ----
    ``dict``：``{"path", "bytes", "sha256", "task_id", "state", "state_source"}``

    异常
    ----
    :class:`WriteDenied` —— 状态非 ``RUNNING``，或权威状态不可用。
    **抛出前不会对目标路径做任何文件系统操作**（不打开、不创建、不删除）。
    """
    target = Path(path)
    source_path = task_state_path()

    state = None
    source_error = None
    try:
        state = read_authoritative_status(task_id)
    except StateSourceError as exc:
        source_error = str(exc)

    if source_error is not None or state != REQUIRED_STATE:
        reason = source_error or f"权威状态为 {state!r}，不是 {REQUIRED_STATE!r}"
        audit_hash, audit_error = _record_denial(
            target, task_id, state, source_path, source_error, reason, task_state
        )
        message = (
            f"写入被拒绝：{reason}；目标文件未被打开、未写入任何字节（{target}）。"
            f"⚠ 状态只认权威文件 {source_path}；"
            f"task_id={task_id!r} 只用于选择读取哪条记录（选择器），不是状态断言。"
            f"⚠ {_caller_note(task_state)}。"
        )
        if audit_error is not None:
            message += f"⚠ 审计日志写入失败：{audit_error!r}"
        raise WriteDenied(
            message,
            path=str(target),
            task_id=task_id,
            state=state,
            state_source=str(source_path),
            audit_hash=audit_hash,
            audit_error=audit_error,
        )

    # ---- 允许路径：临时文件 + 原子替换（避免半写状态） ----
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = content.encode(encoding) if isinstance(content, str) else bytes(content)
    handle_fd, temp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle_fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)  # 原子替换
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise

    return {
        "path": str(target),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "task_id": task_id,
        "state": state,
        "state_source": str(source_path),
    }
