"""真实写文件锁 —— 把 RULE-01 的「判定」变成「拦截」。

背景（RULE-01）
---------------
``gate.py`` 是**判定器**，不是**拦截器**：它能回答「这次写入是否越权」，
但没有任何机制阻止越权**真的发生**（其 ``bounded_scope`` 明文写着
「只做静态字典校验，不负责真正拦截工具调用」）。
本模块补上这句话——**在真正调用写磁盘之前**检查任务状态：

* 状态不是 ``RUNNING`` → 抛 :class:`WriteDenied`，**一个字节都不写盘**，
  并向审计日志追加一条拒绝记录；
* 状态是 ``RUNNING`` → 写临时文件 + :func:`os.replace` 原子替换，避免半写状态。

拒绝路径**不是「写了再回滚」，而是根本不存在任何写入动作**：
不 ``open()`` 目标文件、不 ``mkdir()``、不创建临时文件。

与既有程序的关系
----------------
本模块**不改变** ``gate.py`` / ``objection.py`` / ``dual_judge.py`` / ``audit_log.py`` 的任何行为，
它是新增的**可复用写入通道**。是否把既有写入方接到本锁上，属 task-5 第 3 项
「只报告、不擅自扩大」的范围，由 Mentor / Owner 决定。

失败即拒绝（fail-closed）
-------------------------
状态缺失、``task_state`` 结构不可解析、审计日志写入失败——任何异常情形都**一律拒绝写入**，
绝不「放行以求稳」。审计日志写入失败时仍然抛 :class:`WriteDenied`（拒绝不因留痕失败而消失），
失败原因挂在异常的 ``audit_error`` 属性上。
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from . import audit_log

__all__ = ["locked_write", "WriteDenied", "REQUIRED_STATE", "status_of"]

#: 允许写入所需的唯一状态
REQUIRED_STATE = "RUNNING"

#: 判定「已暂停 / 未批准」时可读到的状态键名
_STATUS_KEYS = ("status", "state")


class WriteDenied(PermissionError):
    """状态非 ``RUNNING`` 时的写入拒绝。

    属性：
        path            被拒绝的目标路径（原样，未做任何文件系统操作）
        state           实际读到的状态（不可解析时为 ``None``）
        required_state  允许写入所需的状态（``RUNNING``）
        audit_hash      拒绝记录在审计日志中的链式哈希；写入失败时为 ``None``
        audit_error     审计日志写入失败的原因；成功时为 ``None``
    """

    def __init__(self, message, *, path=None, state=None, audit_hash=None, audit_error=None):
        super().__init__(message)
        self.path = path
        self.state = state
        self.required_state = REQUIRED_STATE
        self.audit_hash = audit_hash
        self.audit_error = audit_error


def status_of(task_state):
    """从 ``task_state`` 解析任务状态；无法解析时返回 ``None``（由调用方 fail-closed）。

    ``task_state`` 接受两种形式（契约未规定，属本模块的输入约定）：

    * ``str``：直接就是状态名，如 ``"READY"`` / ``"未批准"``；
    * ``dict``：取 ``{"status": ...}`` 或 ``{"state": ...}``。
    """
    if isinstance(task_state, str):
        return task_state.strip() or None
    if isinstance(task_state, dict):
        for key in _STATUS_KEYS:
            value = task_state.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None
    return None


def _record_denial(target, task_state, state, reason):
    """把「写被拒绝」这件事写进审计日志 —— **本函数豁免于本锁**。

    ⚠ 关键设计点（Owner 直接指令点名要求写明）：
    拒绝记录本身写审计日志这一动作，**必须豁免于写锁**。
    否则会陷入鸡生蛋死循环——若连「记一笔拒绝」都要求状态为 ``RUNNING``，
    那么恰恰在**非 RUNNING**（也就是唯一可能发生拒绝的）状态下，
    「我被拒绝了」这件事永远记不下来，锁就从「可审计的拦截」退化成「静默失败」。

    因此这里**直接调用** :func:`control.audit_log.append`，**不经过** :func:`locked_write`；
    审计日志本身是**追加式（append-only）+ 链式哈希**的，豁免范围严格限定为
    「为一次被拒绝的写入追加一条拒绝记录」，**不构成对任意路径的写入放行**。

    记录失败不吞掉：返回 ``(None, 异常对象)``，由调用方挂到 :class:`WriteDenied` 上。
    """
    try:
        event_hash = audit_log.append(
            {
                "kind": "write_lock.denied",
                "path": str(target),
                "state": state,
                "required_state": REQUIRED_STATE,
                "bytes_written": 0,
                "reason": reason,
                "basis": "RULE-01 · task-5（Owner 直接指令）· 拒绝记录豁免于本锁",
            }
        )
        return event_hash, None
    except Exception as exc:  # noqa: BLE001 - 留痕失败必须如实上报，且不得因此放行写入
        return None, exc


def locked_write(path, content, *, task_state, encoding="utf-8"):
    """**状态门控的写文件**：只有任务处于 ``RUNNING`` 才允许落盘。

    参数
    ----
    path       目标文件路径（``str`` 或 ``Path``）
    content    要写入的文本内容（``str``）
    task_state 当前任务状态；``str`` 或 ``{"status": ...}`` 字典（见 :func:`status_of`）
    encoding   文本编码，默认 UTF-8

    返回
    ----
    ``dict``：``{"path": 写入后的绝对路径, "bytes": 写入字节数, "sha256": 写入内容的 SHA256}``

    异常
    ----
    :class:`WriteDenied` —— 状态非 ``RUNNING``（含状态不可解析）。
    **抛出前不会对目标路径做任何文件系统操作**（包括不打开、不创建、不删除）。
    """
    target = Path(path)
    state = status_of(task_state)

    # ---- 状态检查：必须发生在任何文件系统操作之前 ----
    if state != REQUIRED_STATE:
        if state is None:
            reason = f"任务状态不可解析（task_state={task_state!r}）；失败即拒绝（fail-closed）"
        else:
            reason = f"任务状态为 {state!r}，不是 {REQUIRED_STATE!r}"
        audit_hash, audit_error = _record_denial(target, task_state, state, reason)
        message = f"写入被拒绝：{reason}；目标文件未被打开、未写入任何字节（{target}）"
        if audit_error is not None:
            message += f"；⚠ 审计日志写入失败：{audit_error!r}"
        raise WriteDenied(
            message, path=str(target), state=state, audit_hash=audit_hash, audit_error=audit_error
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
    }
