# -*- coding: utf-8 -*-
"""
Mentor 对 Executor 技术异议的正式处置（§8.4 书面回应 + §8.6 补登记 + §8.5 我方判定）

背景：该异议提交于 2026-09-30 08:33，**早于 objection.py 交付**，故无程序生成的 ID。
      Executor 已在其异议附录中请求「objection.py 交付后由程序按其正式结构补登记本异议」。
      本脚本执行该补登记，并显式标注 backfill（§8.6「不由人填写」对本条未满足，须留痕）。

运行：<指定解释器> team/evidence/mentor-dispose-objection.py
"""
import sys
from pathlib import Path

sys.path.insert(0, r"E:\Socratic_M\team\src")
from control import dual_judge, objection  # noqa: E402

RECORD = Path(r"E:\Socratic_M\team\proposals\objection-executor-TASK-001-v1-20260930.md")

# ---------- 1. 补登记（backfill） ----------
res = objection.submit(
    task_id="TASK-001",
    version="v1",
    kind="cannot_execute",
    claim=(
        "TASK-001 v1 对「异议提交」同一动作给出两个相反结论：must_do#1 判 READY→BLOCKED 非法，"
        "must_do#2 要求其发生；且契约不满足其自述生效条件（§10.2-31）。判定：无法执行。"
    ),
    evidence=(
        "① 契约 L31~33 跃迁表 vs L43「置为 BLOCKED」；② V1.4 §10.1 L402 合法集合不含 READY→BLOCKED；"
        "③ CTRL-AUDIT-001 §3 F-02（CONFLICT/阻断）；④ 指定解释器实测无 pytest（exit 1）；"
        "⑤ team/charter/ 无 REQ-T1 记录。全文见 team/proposals/objection-executor-TASK-001-v1-20260930.md"
    ),
    invalidates=(
        "M1 里程碑 objective 在 G36 上落空（§8.4-3 不可执行 → 章程第 4 条争议解决路径入口断裂）；"
        "§9.1「真实性」「质量充分性」两维度不可通过"
    ),
)
OBJ = res["obj_id"]
print("补登记 obj_id:", OBJ)
print("record_path :", res["record_path"])
print("status      :", res["status"], "| previous:", res["task_previous_status"])
print("needs_plan  :", res["needs_mentor_verdict_plan"])

# ---------- 2. §8.4 书面回应 ----------
RESPONSE = """【Mentor 书面回应 · §8.4】异议成立，已受理并处置完毕。异议 ID：%s（backfill）

一、结论：异议**成立**。Executor 判定「无法执行」正确，停止并留痕的行为符合 §8.4 强制触发条款。
    两项主因（F-02 契约内部实质矛盾、契约未满足自述生效条件）经我独立复核，**属实**。

二、技术理由：
    1. F-02：TASK-001 v1 的 must_do#1（跃迁表「其余一律非法」）与 must_do#2（提交即无条件置 BLOCKED）
       在同一动作上结论相反，不存在同时满足两条的实现。Executor 无权改契约文本（§2 禁止项
       「以异议为由自行更改或搁置决策」），故其停止判定正确。
    2. 根因不在契约，而在 **V1.4 基线自身**：§8.4 要求「异议提交即任务进入 BLOCKED（§10.1）」，
       但 §10.1 的合法跃迁集合不含 READY/AUDITING/DRAFT → BLOCKED。此为**基线条文缺陷**。
    3. F-01/F-03：属契约载明不全与上游记录缺失，同为我签发方的责任。

三、处置（全部已执行，非口头承诺）：
    1. **已登记基线缺陷 V1.4-ERR-01**（team/architecture/BASELINE-DEFECTS.md），
       载明 §8.4 与 §10.1 的冲突、根因、处置与未决项（建议 V1.5 正式修订）。
    2. **已签发 TASK-001 v2**，逐条闭合 CTRL-AUDIT-001 全部 9 项缺口：
       F-01 唯一验收环境（指定解释器 + pytest 9.1.1 + 唯一测试命令）；
       F-02 跃迁表补齐（DRAFT|AUDITING|READY→BLOCKED；BLOCKED→进入前状态），并要求实现注释引用 V1.4-ERR-01；
       F-03 建立 team/charter/REQ-records.md，契约改为**引用** REQ-T1 而不再自标层级；
       F-04 路径口径统一基址 E:\\Socratic_M；F-05 补 audit_log.py 交付物与验收项；
       F-06 声明任务内 schema ≠ DEF-06；F-07 定义 G37 三个输入字段；
       F-08 action_tags 改按 §12.2 枚举口径；F-09 声明 tests/ 非受保护验收测试、DEF-02 未关闭。
    3. **已决定 v2 不重审**（team/evidence/mentor-v2-decision.md），理由：9 项均为载明性缺口，
       未引入新技术内容；若每轮整改都再审计一轮，将与 §7.3「禁止制造大量无意义审批」相悖。
       该决定可被 Controller 依 §2.2 提交反证推翻。
    4. **新版本契约已交付并经我独立验收**：TASK-001 v2 六项 must_do 全部实现，74/74 测试通过（我亲自重跑复现）。

四、对「偏差披露」的裁定：
    - Executor 将异议写入 team/proposals/ —— 而 task-2 限定其只写三处。
      **裁定：该偏差成立且必要，不予追究。** 理由：§8.6 要求异议强制留痕，而当时唯一合法的
      留痕落点就是 proposals/；Executor 既无法自动登记（程序未交付）、又不愿放弃留痕，
      且已显式披露。两难之下选择「留痕 + 披露」是正确的取舍。
      同时记入程序偏差 D-P1（见下）。
    - 该文件**予以保留**，不删除（§8.6 留痕不因结果豁免）。

五、程序偏差登记 D-P1（须记录在案）：
    §8.6 要求「异议提交动作即自动登记，由确定性程序捕获，**不由人填写**」。
    本条异议**未满足**该要求——它提交于 objection.py 交付之前，无程序 ID，系手工留痕。
    本次 backfill 由 Mentor 人工发起，**不构成自动登记**。
    ⇒ 该偏差是「用待实现的规则去约束实现该规则的过程」这一时序问题所致，属一次性；
      自 objection.py 交付起，此后所有异议均走程序自动登记。

六、后续：本异议的「是否重大到必须升级 Owner」由 §8.5 双判定裁定（Mentor 与 Controller 各判一次、
    互不可见），判定结果另附。""" % OBJ

r2 = objection.register_mentor_response(OBJ, RESPONSE)
print("mentor response registered:", r2.get("response_registered", r2.get("status", "ok")))

# ---------- 3. §8.5 我方判定（先落盘，不看 Controller） ----------
# ⚠ Controller G-4 整改（2026-09-30）：
#   本脚本曾把 Mentor 的判定内容**内联**在此处，而本脚本在 Controller 的必读清单内，
#   导致 §8.5 / §14 增补 4 的「独立判定、互不可见」**依赖阅读时序而非机制**。
#   该内联判定已被移除；Mentor 的判定只存在于
#       team/agent_state/verdicts/OBJ-20260930-001.mentor.json
#   该文件**明确列入 Controller 的禁读清单**。
#
#   本脚本现在只负责：补登记异议 + 登记 Mentor 书面回应。判定提交已移至
#       team/agent_state/verdicts/submit-mentor-verdict.py（不在任何角色的必读清单内）
#
#   注意：这是一次**流程纪律层面的整改**，不是机制层面的修复。
#   机制层面的缺陷（判定隔离无法由程序强制）已登记为 **G-9**，见 team/README.md 第五节。
print("\n[SECTION 3 REMOVED BY G-4 REMEDIATION]")
print("Mentor 判定见 team/agent_state/verdicts/OBJ-20260930-001.mentor.json（Controller 禁读）")

print("\n" + "=" * 70)
print("OBJ_ID_FOR_CONTROLLER =", OBJ)
print("=" * 70)
