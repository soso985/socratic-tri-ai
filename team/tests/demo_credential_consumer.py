"""task-11 · 规则凭证消费端演示：**只能开 BLOCKED 这一扇门**（闭 CRED-01）。

十段（全部在**隔离数据根**上；凭证一律由**真实的 `compare()`** 以 ``both_minor`` 签发，
**没有一张是手写的**）：

* **S1** ``BLOCKED`` + 有效未消费凭证 → **放行**，状态改变，凭证被标记 ``consumed:true``
* **S2** 再用**同一张**凭证 → **拒绝**（一次性）
* **S3** ``ESCALATED`` + 有效凭证 → **拒绝**
* **S4** ``CHANGE_PENDING`` + 有效凭证 → **拒绝**
* **S5** 「等用户裁决」+ 有效凭证 → **拒绝**
* **S6** 凭证 ``task_id`` 与目标任务不一致 → **拒绝**
* **S7** 不存在的 ``cred_id`` → **拒绝**
* **S8** 两个都没给 → ``ApprovalRequired``（既有行为不变）
* **S9** Owner 批准记录路径仍可用（既有行为不变）
* **S10** 哈希核对：``state_guard`` 已改；``dual_judge`` / ``write_lock`` / ``objection`` / ``gate`` 与快照一致

S3/S4/S5 合起来证明它是「一把只开一扇门的钥匙」，不是万能钥匙。

用法::

    <python> E:\\Socratic_M\\team\\tests\\demo_credential_consumer.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

TEAM_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = TEAM_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from control import dual_judge, objection, state_guard  # noqa: E402
from control.state_guard import (  # noqa: E402
    ApprovalRequired,
    CredentialConsumed,
    CredentialMismatch,
    CredentialNotAllowed,
    CredentialNotFound,
)

SNAPSHOT = TEAM_DIR / "evidence" / "snapshot-before-credential-consumer.json"
TASK = "TASK-001"
MINOR = {"major": False, "hit_rules": [], "scope": "双方均判不重大"}


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def state_file(root: Path) -> Path:
    return root / "agent_state" / "task_state.json"


def set_state(root: Path, status: str, task_id: str = TASK) -> Path:
    path = state_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": "task-internal/v1",
                                "tasks": {task_id: {"status": status}}}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def fresh_root(tag: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix=f"cc-demo-{tag}-"))
    os.environ["CONTROL_DATA_ROOT"] = str(root)
    return root


def cred_file(root: Path, cred_id: str) -> Path:
    matches = sorted((root / "agent_state" / "rule_credentials").glob(f"*.{cred_id}.json"))
    assert len(matches) == 1, matches
    return matches[0]


def issue_real_credential(root: Path, task_id: str = TASK) -> str:
    """用**真实的 compare()** 签发一张规则凭证（both_minor），返回 cred_id。"""
    set_state(root, "BLOCKED", task_id)
    obj_id = objection.submit(task_id, "v2", "premise", "消费端演示用异议（task-11）",
                              "见 TASK-001-credential-consumer.log", "不主张任何失效")["obj_id"]
    set_state(root, "BLOCKED", task_id)
    dual_judge.seal_verdict("mentor", obj_id, MINOR)
    dual_judge.seal_verdict("controller", obj_id, MINOR)
    dual_judge.reveal_verdict("mentor", obj_id, MINOR)
    dual_judge.reveal_verdict("controller", obj_id, MINOR)
    result = dual_judge.compare(obj_id)
    assert result["rule_credential"] is not None, result["rule_credential_skipped_reason"]
    return result["cred_id"]


def show_credential(root: Path, cred_id: str, label: str) -> dict:
    record = json.loads(cred_file(root, cred_id).read_text(encoding="utf-8"))
    print(f"{label}: cred_id={record['cred_id']} issued_by={record['issued_by']} "
          f"basis={record['basis']!r} task_id={record['task_id']} consumed={record['consumed']}")
    return record


def s1() -> tuple:
    print("=" * 78)
    print("S1 · BLOCKED + 有效未消费凭证 → 放行（并消费）")
    print("=" * 78)
    root = fresh_root("s1")
    cred_id = issue_real_credential(root)
    print("数据根          :", root)
    print("凭证来源        : 真实 dual_judge.compare()（both_minor）签发")
    show_credential(root, cred_id, "消费前")
    print("当前状态        :", state_guard.current_status(TASK))

    result = state_guard.request_transition(TASK, "RUNNING", rule_credential_id=cred_id)
    print("request_transition 返回:", json.dumps(
        {k: result[k] for k in ("status", "previous_status", "rule_credential_id",
                                "rule_credential_consumed")}, ensure_ascii=False))
    print("读回状态        :", state_guard.current_status(TASK))
    after = show_credential(root, cred_id, "消费后")
    ok = (result["status"] == "RUNNING" and result["rule_credential_consumed"] is True
          and state_guard.current_status(TASK) == "RUNNING" and after["consumed"] is True)
    print("判定            :", "PASS —— 放行且凭证被消费" if ok else "FAIL")
    print()
    return ok, cred_id, root


def expect_denied(root: Path, status: str, cred_id, exc_type, label: str) -> bool:
    """在给定状态下用凭证尝试解暂停，必须被拒且状态文件不变。"""
    set_state(root, status)
    path = state_file(root)
    before = sha256_of(path)
    try:
        state_guard.request_transition(TASK, "RUNNING", rule_credential_id=cred_id)
        print(f"  {label}: !!! 竟然放行 !!!")
        return False
    except exc_type as exc:
        print(f"  {label}: 抛出 {type(exc).__name__}")
        print(f"      {exc}")
    after = sha256_of(path)
    print(f"  状态文件哈希    : {before[:16]}… → {after[:16]}… | 未变: {before == after}")
    return before == after


def s2(cred_id: str, root: Path) -> bool:
    print("=" * 78)
    print("S2 · 再用同一张凭证 → 拒绝（一次性）")
    print("=" * 78)
    ok = expect_denied(root, "BLOCKED", cred_id, CredentialConsumed, "同一张凭证重放")
    print("判定            :", "PASS —— 一次性凭证不可重放" if ok else "FAIL")
    print()
    return ok


def s3() -> bool:
    print("=" * 78)
    print("S3 · ESCALATED + 有效凭证 → 拒绝")
    print("=" * 78)
    root = fresh_root("s3")
    cred_id = issue_real_credential(root)      # 在 BLOCKED 下签发（有效、未消费）
    show_credential(root, cred_id, "所持凭证")
    ok = expect_denied(root, "ESCALATED", cred_id, CredentialNotAllowed, "ESCALATED + 凭证")
    print("判定            :", "PASS —— 凭证没能打开 ESCALATED" if ok else "FAIL")
    print()
    return ok


def s4() -> bool:
    print("=" * 78)
    print("S4 · CHANGE_PENDING + 有效凭证 → 拒绝")
    print("=" * 78)
    root = fresh_root("s4")
    cred_id = issue_real_credential(root)
    show_credential(root, cred_id, "所持凭证")
    ok = expect_denied(root, "CHANGE_PENDING", cred_id, CredentialNotAllowed, "CHANGE_PENDING + 凭证")
    print("判定            :", "PASS —— 凭证没能打开 CHANGE_PENDING" if ok else "FAIL")
    print()
    return ok


def s5() -> bool:
    print("=" * 78)
    print("S5 · 「等用户裁决」+ 有效凭证 → 拒绝（Owner 点名口径）")
    print("=" * 78)
    root = fresh_root("s5")
    cred_id = issue_real_credential(root)
    show_credential(root, cred_id, "所持凭证")
    ok = expect_denied(root, "等用户裁决", cred_id, CredentialNotAllowed, "等用户裁决 + 凭证")
    print("判定            :", "PASS —— 用户决策中的任务不会被规则凭证放行" if ok else "FAIL")
    print()
    return ok


def s6() -> bool:
    print("=" * 78)
    print("S6 · 凭证 task_id 与目标任务不一致 → 拒绝")
    print("=" * 78)
    root = fresh_root("s6")
    cred_id = issue_real_credential(root, task_id="TASK-OTHER")
    show_credential(root, cred_id, "所持凭证（属于 TASK-OTHER）")
    ok = expect_denied(root, "BLOCKED", cred_id, CredentialMismatch, "TASK-001 使用他任务的凭证")
    print("判定            :", "PASS —— task_id 不符被拒" if ok else "FAIL")
    print()
    return ok


def s7() -> bool:
    print("=" * 78)
    print("S7 · 不存在的 cred_id → 拒绝（fail-closed）")
    print("=" * 78)
    root = fresh_root("s7")
    set_state(root, "BLOCKED")
    path = state_file(root)
    before = sha256_of(path)
    denied = False
    try:
        state_guard.request_transition(TASK, "RUNNING", rule_credential_id="RC-20991231-999")
        print("  !!! 竟然放行 !!!")
    except CredentialNotFound as exc:
        denied = True
        print("  抛出 CredentialNotFound")
        print("     ", exc)
    after = sha256_of(path)
    ok = denied and before == after
    print("判定            :", "PASS —— 查不到即拒绝" if ok else "FAIL")
    print()
    return ok


def s8() -> bool:
    print("=" * 78)
    print("S8 · 两个都没给 → ApprovalRequired（既有行为不变）")
    print("=" * 78)
    root = fresh_root("s8")
    path = set_state(root, "BLOCKED")
    before = sha256_of(path)
    ok = False
    try:
        state_guard.request_transition(TASK, "RUNNING")
    except ApprovalRequired as exc:
        ok = True
        print("  抛出 ApprovalRequired")
        print("     ", exc)
    after = sha256_of(path)
    ok = ok and before == after
    print("判定            :", "PASS —— 既有行为未变" if ok else "FAIL")
    print()
    return ok


def s9() -> bool:
    print("=" * 78)
    print("S9 · Owner 批准记录路径仍可用（既有行为不变）")
    print("=" * 78)
    root = fresh_root("s9")
    set_state(root, "BLOCKED")
    approval = state_guard.record_user_approval(TASK, "演示：解暂停", "Owner",
                                                "task-11 演示用（非真实授权）")
    result = state_guard.request_transition(TASK, "RUNNING", approval_id=approval["approval_id"])
    print("approval_id     :", approval["approval_id"])
    print("返回 status     :", result["status"], "| approval_required:", result["approval_required"])
    print("读回状态        :", state_guard.current_status(TASK))
    print("凭证消费字段    :", result["rule_credential_consumed"], "（批准路径不该动凭证）")
    ok = (result["status"] == "RUNNING" and result["approval_required"] is True
          and result["rule_credential_consumed"] is False
          and state_guard.current_status(TASK) == "RUNNING")
    print("判定            :", "PASS —— 批准路径未受影响" if ok else "FAIL")
    print()
    return ok


def s10() -> bool:
    print("=" * 78)
    print("S10 · 哈希核对（对 evidence/snapshot-before-credential-consumer.json）")
    print("=" * 78)
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    protected = {"dual_judge", "write_lock", "objection", "gate"}
    ok = True
    for key, entry in snap["files"].items():
        path = Path(entry["path"])
        now = sha256_of(path)
        same = now == entry["sha256"]
        if key == "state_guard":
            print(("CHANGED" if not same else "MATCH  "), key, "（本次授权变更对象）")
            continue
        if key in protected:
            ok &= same
            print(("MATCH  " if same else "★CHANGED★"), key, "（受保护模块）")
        else:
            print(("MATCH  " if same else "CHANGED"), key)
    print("四个受保护模块全部与快照一致:", ok)
    print("判定            :", "PASS" if ok else "FAIL")
    print()
    return ok


def main() -> int:
    print("=== task-11 · 规则凭证消费端：只能开 BLOCKED 这一扇门 ===")
    print("解释器版本:", sys.version.split()[0])
    print()
    before_real = sha256_of(TEAM_DIR / "agent_state" / "task_state.json")

    r1, cred_id, root1 = s1()
    r2 = s2(cred_id, root1)
    r3 = s3()
    r4 = s4()
    r5 = s5()
    r6 = s6()
    r7 = s7()
    r8 = s8()
    r9 = s9()
    r10 = s10()

    print("=" * 78)
    print("真实工作区核对（演示全在隔离根）")
    print("=" * 78)
    after_real = sha256_of(TEAM_DIR / "agent_state" / "task_state.json")
    print("真实 task_state.json 哈希:", before_real)
    print("                      （后）:", after_real)
    print("未被改动          :", before_real == after_real)
    print()

    print("=" * 78)
    print("总判定")
    print("=" * 78)
    for label, value in (("S1 BLOCKED + 凭证 → 放行并消费", r1),
                         ("S2 同一张凭证重放 → 拒绝", r2),
                         ("S3 ESCALATED + 凭证 → 拒绝", r3),
                         ("S4 CHANGE_PENDING + 凭证 → 拒绝", r4),
                         ("S5 等用户裁决 + 凭证 → 拒绝", r5),
                         ("S6 task_id 不符 → 拒绝", r6),
                         ("S7 cred_id 不存在 → 拒绝", r7),
                         ("S8 都没给 → ApprovalRequired", r8),
                         ("S9 Owner 批准路径仍可用", r9),
                         ("S10 四个受保护模块与快照一致", r10)):
        print(f"{'PASS' if value else 'FAIL'}  {label}")
    all_pass = all([r1, r2, r3, r4, r5, r6, r7, r8, r9, r10]) and before_real == after_real
    print("总判定:", "PASS" if all_pass else "FAIL")
    print("=" * 78)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
