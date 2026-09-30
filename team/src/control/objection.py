"""§8.6 异议自动登记 —— TASK-001 v2 · must_do #2。

契约：``team/milestones/M1/TASK-001-v2.md``；需求 ``REQ-T1``；基线 V1.4 §8.4 / §8.6 / §10.1。

职能
----
* :func:`submit` —— 异议**提交动作即自动登记**（§8.6 第 1 项：由确定性程序捕获，**不由人填写**）：

  1. 生成异议 ID（``OBJ-YYYYMMDD-NNN``，按当日自增，**不接受调用方传入**——ARC-001 D1）；
  2. 写入 ``proposals/<OBJ-ID>.md``（异议全文，供 §8.6 形式复核与 §11.1 影响清单登记）；
  3. 追加索引到 ``agent_state/objection_index.json``（结构化，供 Controller 机器核对）；
  4. 将 ``task.status`` 置为 ``BLOCKED``，并记录**进入 BLOCKED 前的状态**
     （v2 跃迁表 ``BLOCKED → 进入 BLOCKED 前的状态`` 需要该字段，见 V1.4-ERR-01）。

* :func:`register_mentor_response` —— 登记 Mentor 的书面回应；**异议即使被判「不重大」
  也必须登记**（§8.6：留痕不因结果而豁免）。

实现补充（契约未规定、已在工作报告登记为设计决策）
--------------------------------------------------
* 异议记录结构属 **任务内 schema**，不构成 DEF-06「最小证据 schema」，不关闭该延后项（v2 bounded_scope）。
* ``task_state.json`` 文件名为本实现选定（契约只要求「将状态置为 BLOCKED」，未指定落点文件）。
* 数据根可用环境变量 ``CONTROL_DATA_ROOT`` 重定向（见包 ``__init__`` 说明）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import audit_log, data_root

__all__ = ["submit", "register_mentor_response", "KINDS", "SCHEMA"]

#: 异议类型（v2 must_do#2）→ 中文标签
KINDS = {"premise": "前提有问题", "cannot_execute": "无法执行"}

#: 「无法指认失效对象」的显式取值（ARC-001 D3）
UNKNOWN = "unknown"

#: 记录结构标识（任务内 schema，非 DEF-06）
SCHEMA = "task-internal/v1"

_INDEX_RELATIVE = ("agent_state", "objection_index.json")
_TASK_STATE_RELATIVE = ("agent_state", "task_state.json")
_PROPOSALS_DIR = "proposals"


# ---------------------------------------------------------------- 内部工具


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y%m%d")


def _index_path():
    return data_root().joinpath(*_INDEX_RELATIVE)


def _task_state_path():
    return data_root().joinpath(*_TASK_STATE_RELATIVE)


def _proposals_dir():
    return data_root() / _PROPOSALS_DIR


def _load_json(path, default):
    if not path.exists():
        return json.loads(json.dumps(default))  # 深拷贝默认值
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")


def _require_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必填且必须是非空字符串，收到 {value!r}")
    return value.strip()


def _normalize_invalidates(invalidates):
    """规范化 ``invalidates``（必填），返回 ``(条目列表, 是否为显式 unknown)``。"""
    if invalidates is None:
        raise ValueError("invalidates 必填：须指认「若该异议成立会导致哪条已验收记录或里程碑结论失效」，"
                         "确实指认不出时可显式写 \"unknown\"（ARC-001 D3）")
    raw_items = list(invalidates) if isinstance(invalidates, (list, tuple, set)) else [invalidates]
    items = [str(item).strip() for item in raw_items if item is not None and str(item).strip()]
    if not items:
        raise ValueError("invalidates 必填且不得为空列表；确实指认不出时可显式写 \"unknown\"")
    is_unknown = len(items) == 1 and items[0].lower() == UNKNOWN
    return items, is_unknown


def _next_objection_id() -> str:
    """生成 ``OBJ-YYYYMMDD-NNN``；当日序号自增，取已有记录的最大值 +1。"""
    prefix = f"OBJ-{_today()}-"
    highest = 0

    try:
        index = _load_json(_index_path(), {"objections": []})
    except (OSError, ValueError):
        index = {"objections": []}
    for entry in index.get("objections", []) or []:
        obj_id = str(entry.get("obj_id", ""))
        if obj_id.startswith(prefix):
            suffix = obj_id[len(prefix) :]
            if suffix.isdigit():
                highest = max(highest, int(suffix))

    proposals = _proposals_dir()
    if proposals.is_dir():
        for path in proposals.glob(f"{prefix}*.md"):
            suffix = path.stem[len(prefix) :]
            if suffix.isdigit():
                highest = max(highest, int(suffix))

    return f"{prefix}{highest + 1:03d}"


def _render_record(obj_id, task_id, version, kind, claim, evidence, items, is_unknown,
                   previous_status, submitted_at) -> str:
    lines = [
        f"# 技术异议 {obj_id}",
        "",
        "| 字段 | 内容 |",
        "| --- | --- |",
        f"| **异议 ID** | `{obj_id}` |",
        f"| **task_id / version** | `{task_id}` / `{version}` |",
        f"| **类型** | `{kind}`（{KINDS[kind]}） |",
        "| **提出主体** | Executor |",
        f"| **提交时间** | {submitted_at} |",
        "| **登记方式** | 由 `objection.submit()` 自动登记（§8.6：不由人填写） |",
        f"| **任务状态** | 已置为 `BLOCKED`（进入 BLOCKED 前的状态：{previous_status or '未登记'}） |",
        "",
        "## 主张",
        "",
        claim,
        "",
        "## 证据",
        "",
        evidence,
        "",
        "## 若该异议成立会导致什么失效（§8.4 必填项）",
        "",
    ]
    if is_unknown:
        lines.append(
            "- `unknown` —— 提交方无法指认具体失效对象；"
            "索引已标记 `needs_mentor_verdict_plan=True`，需 Mentor 出具判定方案。"
        )
    else:
        lines.extend(f"- {item}" for item in items)
    lines += [
        "",
        "## Mentor 书面回应（§8.4：不得以沉默方式关闭异议）",
        "",
        "（尚未登记）",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- 对外接口


def submit(task_id, version, kind, claim, evidence, invalidates) -> dict:
    """提交技术异议并**自动登记**，返回登记结果。

    参数（v2 must_do#2 规定的签名）：``task_id`` · ``version`` · ``kind`` · ``claim`` ·
    ``evidence`` · ``invalidates``。

    * ``kind`` ∈ ``{"premise", "cannot_execute"}``；
    * ``invalidates`` **必填**，允许显式写 ``"unknown"``（此时返回
      ``needs_mentor_verdict_plan=True``）；
    * **异议 ID 由本函数生成，不接受调用方传入**；传入 ``obj_id`` 等未知关键字
      会直接抛 ``TypeError``（ARC-001 D1）。
    """
    task_id = _require_text(task_id, "task_id")
    version = _require_text(version, "version")
    if kind not in KINDS:
        raise ValueError(f"kind 必须是 {sorted(KINDS)} 之一，收到 {kind!r}")
    claim = _require_text(claim, "claim（主张）")
    evidence = _require_text(evidence, "evidence（证据）")
    items, is_unknown = _normalize_invalidates(invalidates)

    obj_id = _next_objection_id()
    submitted_at = _now()
    root = data_root()

    # ① 异议全文（含 Mentor 回应位置）写入 proposals/
    proposals = _proposals_dir()
    proposals.mkdir(parents=True, exist_ok=True)
    record_path = proposals / f"{obj_id}.md"

    # ③ 任务置 BLOCKED（先算出「进入 BLOCKED 前的状态」，供 v2 跃迁表回退使用）
    state_path = _task_state_path()
    state = _load_json(state_path, {"schema": SCHEMA, "tasks": {}})
    tasks = state.setdefault("tasks", {})
    existing = tasks.get(task_id) or {}
    existing_status = existing.get("status")
    if existing_status in (None, "BLOCKED"):
        previous_status = existing.get("previous_status")
    else:
        previous_status = existing_status
    tasks[task_id] = {
        "status": "BLOCKED",
        "previous_status": previous_status,
        "contract_version": version,
        "updated_at": submitted_at,
    }
    state["schema"] = SCHEMA
    state["updated_at"] = submitted_at

    record_path.write_text(
        _render_record(obj_id, task_id, version, kind, claim, evidence, items, is_unknown,
                       previous_status, submitted_at),
        encoding="utf-8",
    )

    # ② 结构化索引（§8.6 / §11.1）
    index_path = _index_path()
    index = _load_json(index_path, {"schema": SCHEMA, "objections": []})
    index.setdefault("objections", [])
    entry = {
        "obj_id": obj_id,
        "task_id": task_id,
        "version": version,
        "kind": kind,
        "kind_label": KINDS[kind],
        "claim": claim,
        "evidence": evidence,
        "invalidates": items,
        "invalidates_unknown": is_unknown,
        "needs_mentor_verdict_plan": is_unknown,
        "status": "BLOCKED",
        "task_previous_status": previous_status,
        "submitted_at": submitted_at,
        "record_path": str(record_path),
        "mentor_response": None,
        "mentor_response_at": None,
        "mentor_response_registered": False,
    }
    index["objections"].append(entry)
    index["schema"] = SCHEMA
    index["updated_at"] = submitted_at
    _save_json(index_path, index)
    _save_json(state_path, state)

    # §10.2-36：状态跃迁须写入审计日志（BLOCKED 的进入动作同样是一次跃迁）
    audit_log.append(
        {
            "kind": "objection.submit",
            "obj_id": obj_id,
            "task_id": task_id,
            "contract_version": version,
            "source": previous_status,
            "target": "BLOCKED",
            "basis": "§8.4-3 / §8.6 / V1.4-ERR-01",
        }
    )

    return {
        "obj_id": obj_id,
        "task_id": task_id,
        "version": version,
        "kind": kind,
        "kind_label": KINDS[kind],
        "status": "BLOCKED",
        "task_previous_status": previous_status,
        "invalidates": items,
        "invalidates_unknown": is_unknown,
        "needs_mentor_verdict_plan": is_unknown,
        "record_path": str(record_path),
        "index_path": str(index_path),
        "task_state_path": str(state_path),
        "submitted_at": submitted_at,
    }


def register_mentor_response(obj_id, text) -> dict:
    """登记 Mentor 对某条异议的**书面回应**（§8.4：不得以沉默方式关闭异议）。

    **无论该异议被判「重大」还是「不重大」，回应都必须登记**——留痕不因结果而豁免（§8.6）。
    本函数因此不对任何判定结论做前置条件检查。
    """
    obj_id = _require_text(obj_id, "obj_id")
    text = _require_text(text, "text（Mentor 书面回应）")

    index_path = _index_path()
    index = _load_json(index_path, {"schema": SCHEMA, "objections": []})
    entry = None
    for candidate in index.get("objections", []) or []:
        if candidate.get("obj_id") == obj_id:
            entry = candidate
            break
    if entry is None:
        raise KeyError(f"未登记的异议 ID：{obj_id}（异议必须先经 submit() 自动登记）")

    registered_at = _now()
    record_path = entry.get("record_path") or str(_proposals_dir() / f"{obj_id}.md")
    record = _proposals_dir() / f"{obj_id}.md"

    # 异议（含类型/依据/处置结论）在 proposals/ 中留痕；回应以追加方式登记，不覆盖原文
    if record.exists():
        body = record.read_text(encoding="utf-8")
    else:  # 防御性分支：记录文件被外部删除时仍保证留痕动作发生
        body = _render_record(
            obj_id,
            entry.get("task_id", ""),
            entry.get("version", ""),
            entry.get("kind", "premise"),
            entry.get("claim", ""),
            entry.get("evidence", ""),
            entry.get("invalidates", []),
            bool(entry.get("invalidates_unknown")),
            entry.get("task_previous_status"),
            entry.get("submitted_at", ""),
        ) + "\n> ⚠ 原记录文件缺失，本文件由 `register_mentor_response` 依据索引重建。\n"

    body = body.replace("（尚未登记）", "（见下方登记）", 1)
    body += (
        f"\n### 回应登记 · {registered_at}\n\n"
        "| 字段 | 内容 |\n| --- | --- |\n"
        f"| 登记时间 | {registered_at} |\n"
        "| 登记方式 | `objection.register_mentor_response()`（§8.6 关联字段追加） |\n"
        "| 留痕说明 | 无论该异议被判定为「重大」或「不重大」，本次回应均已登记 |\n\n"
        "**回应全文：**\n\n"
        f"{text}\n"
    )
    record.write_text(body, encoding="utf-8")

    entry["mentor_response"] = text
    entry["mentor_response_at"] = registered_at
    entry["mentor_response_registered"] = True
    index["updated_at"] = registered_at
    _save_json(index_path, index)

    return {
        "obj_id": obj_id,
        "mentor_response_registered": True,
        "mentor_response_at": registered_at,
        "record_path": str(record_path),
        "index_path": str(index_path),
    }
