"""task-9 · 状态护栏演示：暂停免费、解暂停须凭用户批准记录（封 RES-01 实质）。

六个 SECTION（真实输出）：

* **S1** 把 ``TASK-001`` 停在「等用户裁决」，读回权威文件确认，并打印该文件哈希
* **S2** 无批准记录写交付物（任务卡）→ 必须 ``WriteDenied``，改前改后哈希完全相同
* **S3** 程序自行解锁 → 必须 ``ApprovalRequired``，且 ``task_state.json`` **字节不变**
  （证明它连写都没写）
* **S3-附** 多跳绕过分析：为何本实现把触发条件放宽为「任何离开暂停类的跃迁」
* **S4** 伪造批准人（``approved_by="Mentor"``）→ ``InvalidApproval`` 且**不落盘**
* **S5** 有 Owner 批准记录 → **放行**（状态确实变为 ``RUNNING``）
* **S6** 同一 ``approval_id`` 重放 → **被拒**（一次性凭证）

⚠ 本演示跑在**真实工作区**上。S5 会用一条**明确标注为"task-9 验证用、非真实授权"**
的批准记录把 ``TASK-001`` 临时置为 ``RUNNING``，S6 随即把它重新置为暂停态，
因此**演示结束时任务仍处于暂停类**（写入仍被拒绝）。

用法::

    <python> E:\\Socratic_M\\team\\tests\\demo_state_guard.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

TEAM_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = TEAM_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from control import gate, state_guard  # noqa: E402
from control.state_guard import (  # noqa: E402
    ApprovalConsumed,
    ApprovalRequired,
    InvalidApproval,
    current_status,
    record_user_approval,
    request_transition,
    set_paused,
)
from control.write_lock import WriteDenied, locked_write  # noqa: E402

TASK_ID = "TASK-001"

#: 交付物 / 篡改目标：本任务的交付契约（任务卡）
DELIVERABLE = TEAM_DIR / "milestones" / "M1" / "TASK-001-v2.md"

#: 演示用说明 —— 必须让后来者一眼看出这不是 Owner 的真实授权
DEMO_SCOPE = "task-9 验证用（非真实授权）"
DEMO_STATEMENT = (
    "【演示用 · 非真实授权】本条由 Executor 在 task-9 链路验证中生成，"
    "仅用于验证『有用户批准记录才可解暂停』这一机制；"
    "不代表 Owner 对 TASK-001 的任何实际批准，且已被一次性消费。"
)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def state_file() -> Path:
    return TEAM_DIR / "agent_state" / "task_state.json"


def approvals_listing() -> list:
    directory = TEAM_DIR / "agent_state" / "approvals"
    return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []


def s1() -> bool:
    print("=" * 78)
    print("S1 · 停在「等用户裁决」")
    print("=" * 78)
    print("调整前状态      :", current_status(TASK_ID))
    result = set_paused(TASK_ID, "等待用户对 RES-01 护栏的裁决（task-9 演示）")
    print("set_paused 返回 :", json.dumps({k: result[k] for k in ("task_id", "status", "previous_status")},
                                        ensure_ascii=False))
    print("读回权威文件    :", current_status(TASK_ID))
    print("task_state.json :", " ".join(state_file().read_text(encoding="utf-8").split()))
    print("文件 SHA256     :", sha256_of(state_file()))
    ok = current_status(TASK_ID) == state_guard.DEFAULT_PAUSED_STATE
    print("判定            :", "PASS —— 已停在暂停类且无需任何批准" if ok else "FAIL")
    print()
    return ok


def s2() -> bool:
    print("=" * 78)
    print("S2 · 无批准记录写交付物 → 拒绝 + 字节不变")
    print("=" * 78)
    print("交付物（任务卡）:", DELIVERABLE)
    before = sha256_of(DELIVERABLE)
    before_mtime = DELIVERABLE.stat().st_mtime_ns
    print("改前 SHA256     :", before)
    denied = False
    try:
        locked_write(DELIVERABLE, "# 篡改\n", task_id=TASK_ID, task_state={"status": "RUNNING"})
        print("结果            : !!! 竟然写成功 !!!")
    except WriteDenied as exc:
        denied = True
        print("结果            : 抛出 WriteDenied")
        print("异常信息        :", exc)
    after = sha256_of(DELIVERABLE)
    print("改后 SHA256     :", after)
    print("前后完全相同    :", before == after, "| mtime 未变:", before_mtime == DELIVERABLE.stat().st_mtime_ns)
    ok = denied and before == after and before_mtime == DELIVERABLE.stat().st_mtime_ns
    print("判定            :", "PASS —— 无批准时交付物一个字节未变" if ok else "FAIL")
    print()
    return ok


def s3() -> bool:
    print("=" * 78)
    print("S3 · 程序自行解锁 → 拒绝 + 状态文件字节不变")
    print("=" * 78)
    path = state_file()
    before = sha256_of(path)
    before_status = current_status(TASK_ID)
    print("改前 task_state.json SHA256 :", before)
    print("当前状态                    :", before_status)
    print(f"调用：request_transition({TASK_ID!r}, 'RUNNING')   ← 不带 approval_id")
    denied = False
    try:
        request_transition(TASK_ID, "RUNNING")
        print("结果            : !!! 竟然解锁成功 !!!")
    except ApprovalRequired as exc:
        denied = True
        print("结果            : 抛出 ApprovalRequired")
        print("异常信息        :", exc)
    after = sha256_of(path)
    print("改后 task_state.json SHA256 :", after)
    print("前后完全相同                :", before == after)
    print("状态未变                    :", current_status(TASK_ID) == before_status)
    ok = denied and before == after
    print("判定            :", "PASS —— 程序自身没有路径能无凭证解锁（连写都没写）" if ok else "FAIL")
    print()
    return ok


def s3_extra() -> bool:
    """多跳绕过分析：支撑把触发条件放宽为「任何离开暂停类的跃迁」。"""
    print("=" * 78)
    print("S3-附 · 多跳绕过分析（本实现为何收紧触发条件）")
    print("=" * 78)
    hops = [("BLOCKED", "AUDITING"), ("AUDITING", "READY"), ("READY", "RUNNING")]
    print("候选路径：BLOCKED → AUDITING → READY → RUNNING")
    for source, target in hops:
        legal = "G36" not in gate.check({"current_state": source, "next_state": target})["hits"]
        print(f"  §10.1 跃迁表判定 {source:9s} → {target:9s} 合法 = {legal}")
    print("若触发条件只取『当前 ∈ 暂停 且 目标 ∈ 可执行』：")
    print("  · 第 1 跳 BLOCKED → AUDITING：落点 AUDITING 属**非**可执行类 → 无需批准")
    print("  · 第 2、3 跳来源均非暂停类 → 同样无需批准")
    print("  ⇒ 全程**没有任何批准记录**即可到达 RUNNING —— 击穿 Owner 的核心要求。")
    print("本实现把触发条件放宽为『任何离开暂停类的跃迁』后：")
    denied = False
    try:
        request_transition(TASK_ID, "AUDITING")
        print("  第 1 跳结果   : !!! 竟然放行 !!!")
    except ApprovalRequired as exc:
        denied = True
        print("  第 1 跳结果   : 抛出 ApprovalRequired —— 路径在第一跳被截断")
    print("判定            :", "PASS —— 多跳绕已被堵住" if denied else "FAIL")
    print()
    return denied


def s4() -> bool:
    print("=" * 78)
    print("S4 · 伪造批准人（approved_by='Mentor'）→ 拒绝且不落盘")
    print("=" * 78)
    before = approvals_listing()
    print("批准记录（前）  :", before)
    rejected = False
    try:
        record_user_approval(TASK_ID, "解暂停", "Mentor", "我自己批准我自己")
        print("结果            : !!! 竟然被接受 !!!")
    except InvalidApproval as exc:
        rejected = True
        print("结果            : 抛出 InvalidApproval")
        print("异常信息        :", exc)
    after = approvals_listing()
    print("批准记录（后）  :", after)
    print("是否有新文件落盘:", after != before)
    ok = rejected and after == before
    print("判定            :", "PASS —— 非 Owner 批准人被拒且未落盘" if ok else "FAIL")
    print()
    return ok


def s5() -> tuple:
    print("=" * 78)
    print("S5 · 有 Owner 批准记录 → 放行（证明不是「一律拒绝」）")
    print("=" * 78)
    approval = record_user_approval(TASK_ID, DEMO_SCOPE, "Owner", DEMO_STATEMENT)
    approval_id = approval["approval_id"]
    print("批准的出具人    :", approval["approved_by"])
    print("approval_id     :", approval_id)
    print("scope           :", approval["scope"])
    print("statement       :", approval["statement"])
    print("记录文件        :", approval["approval_path"])
    print("consumed（前）  :", approval["consumed"])
    print()
    print("当前状态        :", current_status(TASK_ID))
    result = request_transition(TASK_ID, "RUNNING", approval_id=approval_id)
    print("request_transition 返回:", json.dumps(
        {k: result[k] for k in ("status", "previous_status", "approval_required", "approval_id")},
        ensure_ascii=False))
    status_now = current_status(TASK_ID)
    print("读回状态        :", status_now)
    consumed = json.loads(Path(approval["approval_path"]).read_text(encoding="utf-8"))["consumed"]
    print("consumed（后）  :", consumed)
    ok = status_now == "RUNNING" and consumed is True
    print("判定            :", "PASS —— 有批准记录时确实解暂停成功且凭证被消费" if ok else "FAIL")
    print()
    return ok, approval_id


def s6(approval_id: str) -> bool:
    print("=" * 78)
    print("S6 · 同一 approval_id 重放 → 拒绝（一次性）")
    print("=" * 78)
    set_paused(TASK_ID, "S6 重放测试前重新暂停")
    path = state_file()
    before = sha256_of(path)
    print("重新暂停后状态  :", current_status(TASK_ID))
    print("task_state.json SHA256（前）:", before)
    print(f"重放：request_transition({TASK_ID!r}, 'RUNNING', approval_id={approval_id!r})")
    denied = False
    try:
        request_transition(TASK_ID, "RUNNING", approval_id=approval_id)
        print("结果            : !!! 竟然重放成功 !!!")
    except ApprovalConsumed as exc:
        denied = True
        print("结果            : 抛出 ApprovalConsumed")
        print("异常信息        :", exc)
    after = sha256_of(path)
    print("task_state.json SHA256（后）:", after)
    print("字节不变        :", before == after)
    ok = denied and before == after
    print("判定            :", "PASS —— 一次性凭证不可重放" if ok else "FAIL")
    print()
    return ok


def main() -> int:
    print("=== task-9 · 状态护栏：暂停免费、解暂停须凭用户批准记录 ===")
    print("解释器版本:", sys.version.split()[0])
    print("任务 ID    :", TASK_ID)
    print("运行环境   : 真实工作区", TEAM_DIR)
    print()

    r1 = s1()
    r2 = s2()
    r3 = s3()
    r3x = s3_extra()
    r4 = s4()
    r5, approval_id = s5()
    r6 = s6(approval_id)

    print("=" * 78)
    print("收尾 · 演示结束后的真实状态")
    print("=" * 78)
    print("TASK-001 当前状态:", current_status(TASK_ID), "（暂停类 → 写入仍被拒绝）")
    print("task_state.json  :", " ".join(state_file().read_text(encoding="utf-8").split()))
    print("批准记录清单     :", approvals_listing())
    print("说明             : S5 用过的那条批准记录已 consumed:true 且 statement 明确标注为")
    print("                   「task-9 验证用 · 非真实授权」；演示结束时任务已回到暂停类。")
    print()

    print("=" * 78)
    print("总判定")
    print("=" * 78)
    for label, value in (("S1 停在等用户裁决", r1),
                         ("S2 无批准写交付物被拒且字节不变", r2),
                         ("S3 程序自行解锁被拒且状态文件字节不变", r3),
                         ("S3-附 多跳绕过已堵", r3x),
                         ("S4 伪造批准人被拒且不落盘", r4),
                         ("S5 有批准记录则放行", r5),
                         ("S6 凭证重放被拒", r6)):
        print(f"{'PASS' if value else 'FAIL'}  {label}")
    all_pass = all([r1, r2, r3, r3x, r4, r5, r6])
    print("总判定:", "PASS" if all_pass else "FAIL")
    print("=" * 78)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
