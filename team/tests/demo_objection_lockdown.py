"""task-7 · 链式验证：异议 → 任务进入暂停 → 对任务卡的写入被拒绝。

**本脚本只做验证，不修改任何锁代码**（`write_lock.py`、`gate.py` 一个字符都不动）。

术语：**任务卡** = ``team\\milestones\\M1\\TASK-001-v2.md``（Mentor 指定；当前生效的契约）。

两段：
* **SECTION 1 · 真实工作区**：记录任务卡哈希 → 真提一条异议 → **独立读回** ``task_state.json``
  确认状态 → 用 ``locked_write`` 故意改任务卡 → 必须抛 ``WriteDenied`` → 哈希与开头完全相同。
  ⚠ 真实工作区状态**本来就是 BLOCKED**，因此要如实打印提交前后状态；**若观察不到迁移就直说**。
* **SECTION 2 · 隔离数据根（证明因果）**：
  1. 权威状态置 ``RUNNING`` → 对任务卡**副本**写入 → **应放行**；
  2. 同一根上提异议 → 状态变 ``BLOCKED``；
  3. 对**同一副本路径**再写 → **应被拒**。
  这一步才排除"它一直就是拒的"。

用法::

    <python> E:\\Socratic_M\\team\\tests\\demo_objection_lockdown.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

TEAM_DIR = Path(__file__).resolve().parents[1]              # <工作区>/team
SRC_DIR = TEAM_DIR / "src"
WORKSPACE = TEAM_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from control import data_root, objection  # noqa: E402
from control.write_lock import (  # noqa: E402
    WriteDenied,
    locked_write,
    read_authoritative_status,
    task_state_path,
)

#: 任务卡（Mentor 指定的"你无权更改"的那一份）
TASK_CARD = TEAM_DIR / "milestones" / "M1" / "TASK-001-v2.md"

TASK_ID = "TASK-001"
CONTRACT_VERSION = "v2"
SNAPSHOT = TEAM_DIR / "evidence" / "snapshot-before-objection-lockdown.json"

TAMPER_TEXT = "# 本行若出现在任务卡里，即表示锁失效 —— 任务卡被篡改成功\n"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def state_raw() -> str:
    path = task_state_path()
    return path.read_text(encoding="utf-8") if path.exists() else "<不存在>"


def state_status() -> str:
    """独立读回权威状态（走 locked_write 同一条读取路径）。"""
    try:
        return read_authoritative_status(TASK_ID)
    except Exception as exc:  # noqa: BLE001
        return f"<读取失败：{exc}>"


def index_objections() -> list:
    path = data_root() / "agent_state" / "objection_index.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("objections", [])


def audit_count() -> int:
    path = data_root() / "agent_state" / "audit_log.jsonl"
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def _objection_payload(scope: str) -> dict:
    return dict(
        task_id=TASK_ID,
        version=CONTRACT_VERSION,
        kind="premise",
        claim=(
            f"【task-7 验证用异议 · {scope}】按 Owner 直接指令提交，用于验证"
            "『异议提交 → 任务进入暂停 → 对此后写入的拦截』整条链是否真的走通；"
            "本条**不对 TASK-001 v2 的技术前提作任何实质主张**。"
        ),
        evidence=(
            "验证驱动脚本 team/tests/demo_objection_lockdown.py 及其真实输出 "
            "team/evidence/TASK-001-objection-lockdown.log（task-7）。"
        ),
        invalidates=(
            "不主张任何已验收记录或里程碑结论失效（本条为验证用异议，非实质异议）；"
            "若被误当作实质异议处置，受影响的仅是 TASK-001 的执行状态，而非 §9.1 验收结论。"
        ),
    )


# ================================================================ SECTION 1


def section1_real_workspace() -> bool:
    print("=" * 78)
    print("SECTION 1 · 真实工作区")
    print("=" * 78)
    print("数据根            :", data_root())
    print("任务卡路径        :", TASK_CARD)
    print("权威状态文件      :", task_state_path())
    print()

    card_before = sha256_of(TASK_CARD)
    state_before_raw = state_raw()
    status_before = state_status()
    audit_before = audit_count()
    index_before = len(index_objections())
    proposals_before = sorted(p.name for p in (data_root() / "proposals").glob("OBJ-*.md"))

    print("--- 提交异议前 ---")
    print("任务卡 SHA256     :", card_before)
    print("权威状态（独立读回）:", status_before)
    print("task_state.json   :", " ".join(state_before_raw.split()))
    print("审计日志条目数    :", audit_before)
    print("异议索引条数      :", index_before)
    print("proposals/ 内异议 :", proposals_before)
    print()

    print("--- 真提一条异议（objection.submit）---")
    result = objection.submit(**_objection_payload("真实工作区"))
    print("obj_id            :", result["obj_id"])
    print("登记文件          :", result["record_path"])
    print("索引文件          :", result["index_path"])
    print("返回 status       :", result["status"])
    print("task_previous_status:", result["task_previous_status"])
    print("needs_mentor_verdict_plan:", result["needs_mentor_verdict_plan"])
    print()

    # 独立读回：不信任 submit 的返回值，重新从磁盘读
    state_after_raw = state_raw()
    status_after = state_status()
    print("--- 提交异议后（独立读回磁盘）---")
    print("权威状态（独立读回）:", status_after)
    print("task_state.json   :", " ".join(state_after_raw.split()))
    observed_transition = status_before != status_after
    print("是否观察到状态迁移:", observed_transition,
          "" if observed_transition else f"← **未观察到**（提交前已经是 {status_before}，提交后仍是 {status_after}）")
    print("状态新值是否为暂停态（BLOCKED/CHANGE_PENDING/ESCALATED）:",
          status_after in {"BLOCKED", "CHANGE_PENDING", "ESCALATED"})
    print()

    print("--- 用 locked_write 故意改任务卡（调用方还声称 RUNNING）---")
    print(f"调用：locked_write({str(TASK_CARD)!r}, '<篡改内容>', task_id={TASK_ID!r}, "
          "task_state={'status': 'RUNNING'})")
    raised = None
    try:
        locked_write(TASK_CARD, TAMPER_TEXT, task_id=TASK_ID, task_state={"status": "RUNNING"})
        print("结果：!!! 未抛出异常 —— 任务卡被改写了 !!!")
    except WriteDenied as exc:
        raised = exc
        print("结果：抛出 WriteDenied（任务卡写入被拒）")
        print("异常信息          :", exc)
        print("权威读到 state    :", exc.state)
        print("拒绝记录 audit    :", exc.audit_hash)
    print()

    card_after = sha256_of(TASK_CARD)
    print("--- 任务卡哈希比对 ---")
    print("改前 SHA256       :", card_before)
    print("改后 SHA256       :", card_after)
    print("前后完全相同      :", card_before == card_after)
    print()

    audit_after = audit_count()
    index_after = len(index_objections())
    proposals_after = sorted(p.name for p in (data_root() / "proposals").glob("OBJ-*.md"))
    print("--- 运行期产出（agent_state/ 与 proposals/，属预期）---")
    print("审计日志条目数    :", audit_before, "->", audit_after)
    print("异议索引条数      :", index_before, "->", index_after)
    print("proposals/ 内异议 :", proposals_after)
    print()

    section_pass = (card_before == card_after) and raised is not None
    print("SECTION 1 结论:", "PASS —— 异议已登记，任务卡写入被拒且一个字节未变"
          if section_pass else "FAIL")
    print()
    return section_pass, result["obj_id"], status_before, status_after, observed_transition


# ================================================================ SECTION 2


def section2_isolated() -> bool:
    print("=" * 78)
    print("SECTION 2 · 隔离数据根（证明因果：是异议导致了拒绝）")
    print("=" * 78)

    sandbox = Path(tempfile.mkdtemp(prefix="objection-lockdown-"))
    (sandbox / "agent_state").mkdir(parents=True, exist_ok=True)
    (sandbox / "agent_state" / "task_state.json").write_text(
        json.dumps({"schema": "task-internal/v1",
                    "tasks": {TASK_ID: {"status": "RUNNING"}}}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.environ["CONTROL_DATA_ROOT"] = str(sandbox)

    card_copy = sandbox / "TASK-001-v2.copy.md"
    shutil.copyfile(TASK_CARD, card_copy)          # 只操作**副本**，绝不碰真实任务卡
    copy_initial = sha256_of(card_copy)

    print("隔离数据根        :", sandbox)
    print("权威状态文件      :", task_state_path())
    print("任务卡副本路径    :", card_copy, "（真实任务卡只读复制，不参与写入）")
    print("副本初始 SHA256   :", copy_initial)
    print("权威状态（初始）  :", state_status())
    print()

    # ---- 步骤 1：RUNNING → 应放行 ----
    print("--- 步骤 1：权威状态 RUNNING，对副本写入（应放行）---")
    step1_allowed = False
    step1_error = None
    try:
        r1 = locked_write(card_copy, TAMPER_TEXT, task_id=TASK_ID)
        step1_allowed = True
        print("结果：放行 ✔")
        print("返回              :", json.dumps(r1, ensure_ascii=False))
    except WriteDenied as exc:
        step1_error = exc
        print("结果：被拒绝（不符合预期）:", exc)
    copy_after_step1 = sha256_of(card_copy)
    print("副本 SHA256（步骤1后）:", copy_after_step1)
    print("副本内容确实被写入    :", copy_after_step1 != copy_initial)
    print("副本内容前 40 字符 :", repr(card_copy.read_text(encoding="utf-8")[:40]))
    print()

    # ---- 步骤 2：同一根上提异议 → 状态变 BLOCKED ----
    print("--- 步骤 2：同一隔离根上提异议 ---")
    status_before_obj = state_status()
    obj = objection.submit(**_objection_payload("隔离数据根"))
    status_after_obj = state_status()
    print("obj_id            :", obj["obj_id"])
    print("异议前权威状态    :", status_before_obj)
    print("异议后权威状态    :", status_after_obj)
    print("状态确实发生迁移  :", status_before_obj != status_after_obj)
    print()

    # ---- 步骤 3：同一副本路径再写 → 应被拒 ----
    print("--- 步骤 3：对**同一副本路径**再写（应被拒）---")
    step3_denied = False
    try:
        locked_write(card_copy, TAMPER_TEXT + "# 第二次\n", task_id=TASK_ID,
                     task_state={"status": "RUNNING"})
        print("结果：!!! 未被拒绝 —— 因果链不成立 !!!")
    except WriteDenied as exc:
        step3_denied = True
        print("结果：抛出 WriteDenied ✔")
        print("异常信息          :", exc)
        print("权威读到 state    :", exc.state)
    copy_after_step3 = sha256_of(card_copy)
    print("副本 SHA256（步骤3后）:", copy_after_step3)
    print("步骤 2→3 之间副本未被改动:", copy_after_step3 == copy_after_step1)
    print()

    section_pass = step1_allowed and step1_error is None and step3_denied \
        and copy_after_step3 == copy_after_step1 and status_before_obj != status_after_obj
    print("SECTION 2 结论:", "PASS —— RUNNING 时放行、提异议后同一路径被拒（因果成立）"
          if section_pass else "FAIL")
    print()
    os.environ.pop("CONTROL_DATA_ROOT", None)      # 恢复真实数据根
    return section_pass


# ================================================================ 快照比对


def snapshot_check() -> bool:
    print("=" * 78)
    print("快照比对 · evidence/snapshot-before-objection-lockdown.json（Mentor 存留）")
    print("=" * 78)
    data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    must_match = {"lock", "taskcard"}
    ok = True
    for key, entry in data["files"].items():
        path = Path(entry["path"])
        actual = sha256_of(path)
        same = actual == entry["sha256"]
        tag = "MATCH  " if same else "CHANGED"
        note = ""
        if not same and key == "taskstate":
            note = "（预期：异议提交由程序写入状态文件）"
        elif not same and key not in must_match:
            note = "（非必须一致项）"
        elif not same:
            note = "★违反要求★"
        if not same and key in must_match:
            ok = False
        print(f"{tag} {key:10s} {path.name}")
        print(f"        快照: {entry['sha256']}")
        print(f"        现状: {actual}{('  ' + note) if note else ''}")
    print()
    print("必须一致的两项（lock / taskcard）均未改动:", ok)
    print()
    return ok


def main() -> int:
    print("=== task-7 · 链式验证：异议 → 暂停 → 写入拦截 ===")
    print("解释器版本:", sys.version.split()[0])
    print("任务卡      :", TASK_CARD)
    print()
    inherited = os.environ.pop("CONTROL_DATA_ROOT", None)
    if inherited:
        print(f"⚠ 环境变量 CONTROL_DATA_ROOT 原为 {inherited!r} —— SECTION 1 要求真实工作区，已临时移除")
        print()

    s1_pass, obj_id, status_before, status_after, observed = section1_real_workspace()
    s2_pass = section2_isolated()
    snap_pass = snapshot_check()

    print("=" * 78)
    print("总判定")
    print("=" * 78)
    print(f"SECTION 1（真实工作区）：{'PASS' if s1_pass else 'FAIL'}")
    print(f"  任务卡路径        : {TASK_CARD}")
    print(f"  异议 obj_id       : {obj_id}")
    print(f"  异议前/后状态     : {status_before} / {status_after}")
    print(f"  是否观察到状态迁移: {observed}" + ("" if observed else "（提交前已是暂停态，如实记录）"))
    print(f"SECTION 2（隔离·因果）：{'PASS' if s2_pass else 'FAIL'}")
    print(f"  步骤 2 是否确实放行: {'是' if s2_pass else '见上文'}")
    print(f"快照比对（lock/taskcard 必须一致）：{'PASS' if snap_pass else 'FAIL'}")
    print(f"总判定            : {'PASS' if (s1_pass and s2_pass and snap_pass) else 'FAIL'}")
    print("=" * 78)
    return 0 if (s1_pass and s2_pass and snap_pass) else 1


if __name__ == "__main__":
    sys.exit(main())
