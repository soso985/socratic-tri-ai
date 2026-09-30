# -*- coding: utf-8 -*-
"""
Mentor 独立验收脚本 · TASK-001 v2 勘误 01（V1.4-ERR-02）
依据：勘误文件第四节「按 §9.1 由 Mentor 独立验收，亲自重跑，并亲自构造三条新跃迁验证其为合法」。

独立构造，不复用 Executor 的测试代码。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"E:\Socratic_M\team\src")
import control  # noqa: E402
from control import gate  # noqa: E402

control.set_data_root(Path(tempfile.mkdtemp(prefix="mentor-erratum-")))

results = []


def rec(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def t(src, dst, blocked_from=None):
    task = {
        "task_id": "T-ERR", "contract_status": "APPROVED",
        "contract_version": "v2", "task_version": "v2",
        "allowed_paths": ["team/src/"], "write_paths": ["team/src/a.py"],
        "dependencies_met": True, "current_state": src, "next_state": dst,
        "awaiting_user_decision": False, "is_paused": False,
        "report": {"changes": 1, "tests": 1, "risks": 1, "deviations": 1, "rollback": 1},
    }
    if blocked_from is not None:
        task["blocked_from"] = blocked_from
    return task


print("=" * 78)
print("Mentor 独立验收 · TASK-001 v2 勘误 01（V1.4-ERR-02）")
print("=" * 78)

# ---------- 本次勘误的三条新跃迁：必须为合法 ----------
for src, dst, why in [("REWORK", "RUNNING", "返工完成（§9.2/§9.3）"),
                      ("ESCALATED", "READY", "用户裁决继续（§6.1）"),
                      ("ESCALATED", "BLOCKED", "用户裁决暂停（§6.5）")]:
    r = gate.check(t(src, dst))
    rec(f"新跃迁合法：{src} → {dst}（{why}）",
        "G36" not in r["hits"], f"hits={r['hits']}")

# ---------- 反向守卫：不得放宽其他规则 ----------
for src, dst in [("SUBMITTED", "RUNNING"), ("REWORK", "SUBMITTED"), ("REWORK", "ACCEPTED"),
                 ("ESCALATED", "RUNNING"), ("ESCALATED", "ACCEPTED"), ("ACCEPTED", "RUNNING"),
                 ("DRAFT", "RUNNING"), ("SUBMITTED", "DRAFT")]:
    r = gate.check(t(src, dst))
    rec(f"仍非法：{src} → {dst}", "G36" in r["hits"], f"hits={r['hits']}")

# ---------- 集合精确断言：恰为 v2 原 17 条 + 勘误 3 条 = 20 条 ----------
lt = gate.LEGAL_TRANSITIONS
rec("LEGAL_TRANSITIONS 恰为 20 条（17 + 3）", len(lt) == 20, f"实际 {len(lt)} 条")
for pair in [("REWORK", "RUNNING"), ("ESCALATED", "READY"), ("ESCALATED", "BLOCKED")]:
    rec(f"集合含 {pair[0]}→{pair[1]}", pair in lt)

# ---------- 确认 ERR-01 的处置未被回退 ----------
for src, dst in [("DRAFT", "BLOCKED"), ("AUDITING", "BLOCKED"), ("READY", "BLOCKED")]:
    r = gate.check(t(src, dst, blocked_from=src))
    rec(f"ERR-01 处置未回退：{src} → BLOCKED", "G36" not in r["hits"], f"hits={r['hits']}")
r = gate.check(t("BLOCKED", "READY", blocked_from="READY"))
rec("ERR-01 处置未回退：BLOCKED → 进入前状态", "G36" not in r["hits"], f"hits={r['hits']}")

# ---------- G37 未被回退 ----------
task = t("RUNNING", "SUBMITTED")
task["awaiting_user_decision"] = True
task["is_paused"] = False
r = gate.check(task)
rec("G37 未被回退：等待决策 + 未暂停 → 阻断", "G37" in r["hits"], f"hits={r['hits']}")

# ---------- 源码引用 V1.4-ERR-02 ----------
src = Path(r"E:\Socratic_M\team\src\control\gate.py").read_text(encoding="utf-8")
rec("gate.py 源码引用 V1.4-ERR-02", "V1.4-ERR-02" in src)
rec("gate.py 同时保留 V1.4-ERR-01 引用", "V1.4-ERR-01" in src)

print("=" * 78)
passed = sum(1 for _, ok, _ in results if ok)
print(f"Mentor 独立验收（勘误 01）：{passed} / {len(results)} 项通过")
fails = [(n, d) for n, ok, d in results if not ok]
for n, d in fails:
    print(f"  FAIL: {n} :: {d}")
print("=" * 78)
sys.exit(0 if not fails else 1)
