# -*- coding: utf-8 -*-
"""Mentor 对 OBJ-20260930-002 的 §8.4 书面回应（该异议为 task-7 链路验证所产生）。"""
import sys

sys.path.insert(0, r"E:\Socratic_M\team\src")
from control import objection  # noqa: E402

OBJ = "OBJ-20260930-002"

RESPONSE = """【Mentor 书面回应 · §8.4】异议已处置。异议 ID：OBJ-20260930-002

一、性质认定：**该条为 task-7 链路验证产生的验证用异议，不是实质技术异议。**
    Executor 已在异议正文标注「task-7 验证用异议…不对 TASK-001 v2 的技术前提作任何实质主张」，
    并在 invalidates 中声明「不主张任何已验收记录或里程碑结论失效」（needs_mentor_verdict_plan=False）。
    经我复核：该标注属实，未留假的待办标记。

二、为何它仍是被真实登记的异议：
    Owner 直接指令要求「执行者提交一条异议，之后再去改任务卡」，因此必须走真实的
    objection.submit 通道——用假对象或绕过程序都会使验证失去意义。故本条记录**照常登记、照常回应**，
    不因"验证用"而豁免（§8.6：留痕不因结果豁免）。

三、技术理由（针对通道本身，而非 TASK-001 v2）：
    1. 异议提交后，authoritative task_state.json 的 tasks.TASK-001.status 被置为 BLOCKED；
    2. 随后对任务卡 milestones/M1/TASK-001-v2.md 的写入被 write_lock 拒绝；
    3. 任务卡 SHA256 前后一致（0A5CFA758E1404A3…），字节数与 mtime 均未变。
    这三步由 Executor 的 evidence/TASK-001-objection-lockdown.log 记录，并由我以
    evidence/mentor-verify-lockdown.py 独立复现（16/16 PASS）——**含因果对照**：
    隔离根 RUNNING 时同一路径可写 → 提交异议 → 同根同路径被拒。

四、处置结论：**接受，不触发返工，不构成 §8.5 升级事由。**
    本条不涉及 L0/L1 变更、不越 allowed_paths、不命中 §12.2、不超预算、不涉不可逆操作。

五、由本条暴露的真实缺口（登记为 KIND-01）：
    §8.4 的 kind 枚举只有 {premise, cannot_execute}，**没有"验证/行政用途"类别**。
    Executor 为不违反接口约束只能选 premise，语义并不准确。
    后果：今后每次链路自测都会往异议通道塞一条语义不符的记录，并触发我不必要的回应义务。
    **建议**：为 kind 增加 `verification` 类别，或为链路自测另设通道。当前未擅自扩枚举。

六、附带说明（登记为 TSV-01，低）：
    本次 objection.submit(version="v2") 使 task_state.json 的 contract_version 由 v1 覆写为 v2
    （status 未变，仍为 BLOCKED）。Executor 按"v1 已作废、当前生效为 v2"填写，判断正确。
    但该字段**每次异议都会被覆写**，若同一任务在不同契约版本下多次提异议，只保留最后一次——
    属低severity 观察，登记备查。"""

r = objection.register_mentor_response(OBJ, RESPONSE)
print("registered:", r.get("status", "ok"))
print("obj_id:", OBJ)
