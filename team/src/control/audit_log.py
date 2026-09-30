"""§10.2-36 后半句：不可随意覆盖的审计日志（链式哈希）—— TASK-001 v2 · must_do #4。

V1.4 §10.2-36 完整表述是「所有状态跃迁由确定性程序校验**并写入不可随意覆盖的审计日志**」。
v1 契约只承接了前半句（CTRL-AUDIT-001 缺口 F-05），v2 补齐本模块。

设计
----
* 追加式 JSONL：``agent_state/audit_log.jsonl``，**只追加、从不重写**。
* 链式哈希：第 *n* 条记录含前一条的 ``hash``；``hash_n = sha256(prev_hash + "|" + 规范化(event))``。
  创世前驱为 64 个 ``0``。
* 篡改任意一条记录的 ``event`` 内容都会使该条及其之后所有记录的链式校验失败，
  因此 ``verify()`` 返回 ``False``。

契约：``team/milestones/M1/TASK-001-v2.md``（must_do#4、验收项 7）
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from . import data_root

__all__ = ["append", "verify", "GENESIS", "LOG_RELATIVE_PATH"]

#: 创世前驱哈希
GENESIS = "0" * 64

#: 审计日志相对数据根的路径
LOG_RELATIVE_PATH = ("agent_state", "audit_log.jsonl")


def _log_path():
    return data_root().joinpath(*LOG_RELATIVE_PATH)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _canonical(event) -> str:
    """事件规范化序列化（键排序、无多余空白），保证哈希可复现。"""
    return json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _chain_hash(prev_hash: str, event) -> str:
    payload = f"{prev_hash}|{_canonical(event)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_records(path):
    """读取全部记录；任一行为空则跳过，解析失败抛 ``ValueError``。"""
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError as exc:  # pragma: no cover - 防御性分支
                raise ValueError(f"审计日志第 {lineno} 行不是合法 JSON：{exc}") from exc
    return records


def append(event: dict) -> str:
    """追加一条审计事件，返回该事件的链式哈希。

    ``event`` 必须为 dict。文件不存在时自动创建（含父目录）。
    本函数**从不修改已有记录**——「不可随意覆盖」即由此保证。
    """
    if not isinstance(event, dict):
        raise TypeError(f"event 必须是 dict，收到 {type(event).__name__}")

    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    prev_hash = GENESIS
    seq = 1
    if path.exists():
        records = _read_records(path)
        if records:
            prev_hash = records[-1].get("hash", GENESIS)
            seq = int(records[-1].get("seq", len(records))) + 1

    event_hash = _chain_hash(prev_hash, event)
    record = {
        "seq": seq,
        "ts": _now(),
        "event": event,
        "prev_hash": prev_hash,
        "hash": event_hash,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(_canonical(record) + "\n")
    return event_hash


def verify(event_hash: str) -> bool:
    """校验指定事件**及其之前链条**是否未被篡改。

    返回 ``True`` 仅当：日志中存在该 ``hash``，且从创世记录到该条为止的重算链条
    与落盘链条完全一致。
    ``hash`` 不存在、日志缺失、任一行非法、或链条在该条（含）之前断裂 → ``False``。
    """
    if not isinstance(event_hash, str) or not event_hash:
        return False

    path = _log_path()
    if not path.exists():
        return False

    try:
        records = _read_records(path)
    except (OSError, ValueError):
        return False

    prev_hash = GENESIS
    for record in records:
        if not isinstance(record, dict):
            return False
        try:
            stored_hash = record["hash"]
            stored_prev = record["prev_hash"]
            event = record["event"]
        except KeyError:
            return False
        if stored_prev != prev_hash:
            return False
        if _chain_hash(prev_hash, event) != stored_hash:
            return False
        if stored_hash == event_hash:
            return True
        prev_hash = stored_hash
    return False
