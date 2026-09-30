# -*- coding: utf-8 -*-
"""
Mentor 独立验收脚本 · TASK-001 v2
依据：ARC-001 §4 的五项验收动作；V1.4 §9.1「Mentor 必须独立查看源文件与变更、
      在适用条件下重新执行验收测试，不能只读 Executor 自述」。

本脚本由 Mentor 亲自编写，不复用 Executor 的测试代码，独立构造场景。
运行：<指定解释器> team/evidence/mentor-verify-TASK-001.py
"""
import json
import sys
import tempfile
from pathlib import Path

SRC = Path(r"E:\Socratic_M\team\src")
sys.path.insert(0, str(SRC))

import control  # noqa: E402
from control import audit_log, dual_judge, gate, objection  # noqa: E402
from control.dual_judge import IncompleteJudgment  # noqa: E402

# 独立数据根，绝不写入 team/agent_state
TMP = Path(tempfile.mkdtemp(prefix="mentor-verify-"))
control.set_data_root(TMP)

results = []


def rec(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


print("=" * 78)
print("Mentor 独立验收 · TASK-001 v2")
print("数据根（隔离）:", TMP)
print("=" * 78)

# ---------- ARC-001 §4-2：亲自构造 G37 命中 ----------
t_hit = {
    "task_id": "T-DEMO",
    "contract_status": "APPROVED",
    "contract_version": "v2",
    "task_version": "v2",
    "allowed_paths": ["team/src/"],
    "write_paths": ["team/src/a.py"],
    "dependencies_met": True,
    "current_state": "RUNNING",
    "next_state": "SUBMITTED",
    "awaiting_user_decision": True,
    "is_paused": False,
    "report": {"changes": 1, "tests": 1, "risks": 1, "deviations": 1, "rollback": 1},
}
r = gate.check(t_hit)
rec("G37 命中：等待用户决策 + 未暂停 → 阻断",
    ("G37" in r["hits"]) and r["blocked"] is True,
    f"hits={r['hits']} blocked={r['blocked']}")

# ---------- §4-2 反例：已暂停则不阻断 ----------
t_ok = dict(t_hit)
t_ok["is_paused"] = True
r = gate.check(t_ok)
rec("G37 不命中：等待用户决策 + 已暂停 → 不因 G37 阻断",
    "G37" not in r["hits"],
    f"hits={r['hits']}")

# ---------- §4-5 状态跃迁：v2 追加项 ----------
t = dict(t_hit)
t.update({"awaiting_user_decision": False, "is_paused": False,
          "current_state": "READY", "next_state": "BLOCKED", "blocked_from": "READY"})
r = gate.check(t)
rec("跃迁 READY→BLOCKED 合法（v2 依 V1.4-ERR-01 追加）",
    "G36" not in r["hits"], f"hits={r['hits']}")

t2 = dict(t)
t2.update({"current_state": "SUBMITTED", "next_state": "RUNNING"})
r = gate.check(t2)
rec("跃迁 SUBMITTED→RUNNING 非法 → G36 命中",
    "G36" in r["hits"], f"hits={r['hits']}")

# ---------- 核对 Executor 登记的风险 R-1：死端 ----------
t3 = dict(t)
t3.update({"current_state": "REWORK", "next_state": "RUNNING"})
r = gate.check(t3)
rec("R-1 复核：REWORK→RUNNING 被判非法（死端确实存在）",
    "G36" in r["hits"], f"hits={r['hits']}  ← 与 Executor 登记的 R-1 一致")

t4 = dict(t)
t4.update({"current_state": "ESCALATED", "next_state": "READY"})
r = gate.check(t4)
rec("R-1 复核：ESCALATED→READY 被判非法（死端确实存在）",
    "G36" in r["hits"], f"hits={r['hits']}")

# ---------- §4-4：异议 ID 不可外部指定 ----------
try:
    objection.submit(task_id="T-DEMO", version="v2", kind="premise",
                     claim="c", evidence="e", invalidates="unknown",
                     obj_id="OBJ-FORGED-001")
    rec("异议 ID 不可外部指定", False, "竟然接受了外部传入的 obj_id")
except TypeError as exc:
    rec("异议 ID 不可外部指定", True, f"TypeError: {exc}")
except Exception as exc:  # noqa: BLE001
    rec("异议 ID 不可外部指定", False, f"抛出的不是 TypeError 而是 {type(exc).__name__}: {exc}")

# ---------- 异议登记：ID 格式 / 置 BLOCKED ----------
o1 = objection.submit(task_id="T-DEMO", version="v2", kind="premise",
                      claim="Mentor 独立验收构造的异议", evidence="由验收脚本注入",
                      invalidates="unknown")
oid = o1.get("objection_id") or o1.get("obj_id") or ""
rec("异议 ID 由程序生成且格式正确",
    oid.startswith("OBJ-") and len(oid.split("-")) == 3,
    f"obj_id={oid}")

rec("异议提交后任务置为 BLOCKED 并记录进入前状态",
    (o1.get("status") == "BLOCKED") and ("task_previous_status" in o1),
    f"status={o1.get('status')} previous={o1.get('task_previous_status')!r}")

rec("invalidates=unknown 时置 needs_mentor_verdict_plan",
    o1.get("needs_mentor_verdict_plan") is True,
    f"flag={o1.get('needs_mentor_verdict_plan')}")

o2 = objection.submit(task_id="T-DEMO", version="v2", kind="cannot_execute",
                      claim="第二条", evidence="e2", invalidates="unknown")
rec("异议 ID 自增（第二条不同于第一条）",
    (o2.get("objection_id") or o2.get("obj_id")) != oid,
    f"{oid} -> {o2.get('objection_id') or o2.get('obj_id')}")

# ---------- §4-3：单方提交 → IncompleteJudgment ----------
try:
    dual_judge.compare(oid)
    rec("单方未提交时 compare 拒绝执行", False, "未抛异常")
except IncompleteJudgment as exc:
    rec("单方未提交时 compare 拒绝执行", True, f"IncompleteJudgment: {exc}")

dual_judge.submit_verdict("mentor", oid, {"major": False, "hit_rules": [], "scope": "T-DEMO"})
try:
    dual_judge.compare(oid)
    rec("仅 Mentor 提交时 compare 仍拒绝", False, "未抛异常")
except IncompleteJudgment:
    rec("仅 Mentor 提交时 compare 仍拒绝", True, "IncompleteJudgment")

# ---------- 三种 outcome ----------
cases = [("controller", {"major": True, "hit_rules": ["G32"], "scope": "T-DEMO"}, "divergent"),
         ("mentor", {"major": True, "hit_rules": ["G32"], "scope": "T-DEMO"}, "both_major")]
for role, verdict, expect in cases:
    dual_judge.submit_verdict(role, oid, verdict)
    res = dual_judge.compare(oid)
    rec(f"compare 产出 {expect}", res.get("outcome") == expect, f"outcome={res.get('outcome')}")

oid2 = objection.submit(task_id="T-DEMO", version="v2", kind="premise",
                        claim="用于测 both_minor", evidence="e", invalidates="unknown")
oid2 = oid2.get("objection_id") or oid2.get("obj_id")
dual_judge.submit_verdict("mentor", oid2, {"major": False, "hit_rules": [], "scope": "s"})
dual_judge.submit_verdict("controller", oid2, {"major": False, "hit_rules": [], "scope": "s"})
res = dual_judge.compare(oid2)
rec("compare 产出 both_minor 且不升级",
    res.get("outcome") == "both_minor" and res.get("escalate") is False,
    f"outcome={res.get('outcome')} escalate={res.get('escalate')}")

# ---------- 审计日志抗篡改 ----------
h = audit_log.append({"action": "mentor-verify", "seq": 1})
rec("审计日志 append 返回 hash 且 verify 为真",
    audit_log.verify(h) is True, f"hash={h[:16]}")

log_path = TMP / "agent_state" / "audit_log.jsonl"
raw = log_path.read_text(encoding="utf-8")
tampered = raw.replace("mentor-verify", "TAMPERED-BY-MENTOR")
log_path.write_text(tampered, encoding="utf-8")
rec("审计日志被篡改后 verify 返回 False",
    audit_log.verify(h) is False, "篡改 action 字段后校验失败")

# ---------- 汇总 ----------
print("=" * 78)
passed = sum(1 for _, ok, _ in results if ok)
print(f"Mentor 独立验收：{passed} / {len(results)} 项通过")
fails = [(n, d) for n, ok, d in results if not ok]
if fails:
    print("失败项：")
    for n, d in fails:
        print(f"  - {n}  :: {d}")
print("数据根隔离目录：", TMP)
print("=" * 78)
sys.exit(0 if not fails else 1)
