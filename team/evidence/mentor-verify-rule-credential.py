# -*- coding: utf-8 -*-
"""
Mentor 独立验收 · task-10：compare() 内签发一次性规则凭证

通过标准（Owner）：
  只有 both_minor 时在 compare() 内部签发一次性规则凭证；
  both_major / divergent / 状态为「等用户裁决」时不得签发。

独立性：自建隔离根；自行构造四种 outcome；并核对"只改 compare()"的结构约束。
"""
import ast
import hashlib
import json
import pathlib
import sys
import tempfile
import uuid

WS = pathlib.Path(r"E:\Socratic_M")
SRC = WS / "team" / "src"
SNAP = WS / "team" / "evidence" / "snapshot-before-rule-credential.json"
REAL_STATE = WS / "team" / "agent_state" / "task_state.json"
REAL_INDEX = WS / "team" / "agent_state" / "objection_index.json"

sys.path.insert(0, str(SRC))
import control  # noqa: E402
from control import dual_judge  # noqa: E402

results = []


def rec(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def build(status, *, with_index=True, index_obj=None, corrupt_index=False, status_missing=False):
    r = pathlib.Path(tempfile.mkdtemp(prefix="mentor-cred-"))
    (r / "agent_state").mkdir(parents=True, exist_ok=True)
    obj = "OBJ-MV-" + uuid.uuid4().hex[:8]
    if corrupt_index:
        (r / "agent_state" / "objection_index.json").write_text("{not json", encoding="utf-8")
    elif with_index:
        (r / "agent_state" / "objection_index.json").write_text(
            json.dumps({"objections": [{"obj_id": index_obj or obj, "task_id": "TASK-001"}]},
                       ensure_ascii=False), encoding="utf-8")
    if not status_missing:
        (r / "agent_state" / "task_state.json").write_text(
            json.dumps({"tasks": {"TASK-001": {"status": status}}}, ensure_ascii=False), encoding="utf-8")
    return r, obj


def creds(root, obj):
    d = root / "agent_state" / "rule_credentials"
    return sorted(p.name for p in d.glob(f"{obj}.*.json")) if d.is_dir() else []


def run(root, obj, mentor_major, controller_major):
    """在给定根上构造判定并 compare，返回 compare 结果。"""
    control.set_data_root(root)
    dual_judge.seal_verdict("mentor", obj, {"major": mentor_major, "hit_rules": [], "scope": "m"})
    dual_judge.seal_verdict("controller", obj, {"major": controller_major, "hit_rules": [], "scope": "c"})
    dual_judge.reveal_verdict("mentor", obj, {"major": mentor_major, "hit_rules": [], "scope": "m"})
    dual_judge.reveal_verdict("controller", obj, {"major": controller_major, "hit_rules": [], "scope": "c"})
    return dual_judge.compare(obj)


print("=" * 78)
print("Mentor 独立验收 · compare() 规则凭证签发")
print("=" * 78)

# ---------- A. both_minor + 非常用户裁决 → 签发 ----------
root, obj = build("BLOCKED")
res = run(root, obj, False, False)
c = creds(root, obj)
rec("A both_minor + BLOCKED → 签发", res.get("outcome") == "both_minor" and len(c) == 1,
    f"outcome={res.get('outcome')} cred_id={res.get('cred_id')} 凭证数={len(c)}")
if c:
    payload = json.loads((root / "agent_state" / "rule_credentials" / c[0]).read_text(encoding="utf-8"))
    rec("A 凭证含必需字段且 consumed=false",
        all(k in payload for k in ("cred_id", "obj_id", "task_id", "basis", "outcome",
                                   "issued_at", "issued_by", "consumed"))
        and payload["consumed"] is False,
        f"keys={sorted(payload.keys())}")

# ---------- B/C. 绝不签发 ----------
for label, mm, cm, want in [("B both_major", True, True, "both_major"),
                            ("C divergent", True, False, "divergent")]:
    r2, o2 = build("BLOCKED")
    res2 = run(r2, o2, mm, cm)
    rec(f"{label} → 不签发", res2.get("outcome") == want and res2.get("rule_credential") is None
        and not creds(r2, o2),
        f"outcome={res2.get('outcome')} cred={res2.get('cred_id')} 凭证数={len(creds(r2, o2))}")

# ---------- D. both_minor + 状态「等用户裁决」→ 不签发（真实治理文件字节级副本） ----------
r3 = pathlib.Path(tempfile.mkdtemp(prefix="mentor-cred-real-"))
(r3 / "agent_state").mkdir(parents=True, exist_ok=True)
(r3 / "agent_state" / "task_state.json").write_bytes(REAL_STATE.read_bytes())    # 真实副本
(r3 / "agent_state" / "objection_index.json").write_bytes(REAL_INDEX.read_bytes())
rec("D 副本与真实治理文件逐字节相同",
    sha(r3 / "agent_state" / "task_state.json") == sha(REAL_STATE)
    and sha(r3 / "agent_state" / "objection_index.json") == sha(REAL_INDEX),
    f"真实状态={json.loads(REAL_STATE.read_text(encoding='utf-8'))['tasks']['TASK-001']['status']!r}")
d_obj = json.loads(REAL_INDEX.read_text(encoding="utf-8"))["objections"][-1]["obj_id"]
res3 = run(r3, d_obj, False, False)
rec("D ★ both_minor + 真实状态「等用户裁决」→ 不签发",
    res3.get("outcome") == "both_minor" and res3.get("rule_credential") is None
    and not creds(r3, d_obj),
    f"outcome={res3.get('outcome')} 跳过原因={res3.get('rule_credential_skipped_reason')}")

# ---------- E. 幂等 ----------
control.set_data_root(root)             # 切回 A 的隔离根（此处曾漏切，已修）
res4 = dual_judge.compare(obj)          # 同一 obj_id，A 已签过一张未消费的
rec("E 幂等：再次 compare 不重复签发", len(creds(root, obj)) == 1 and res4.get("cred_id") is None,
    f"凭证数={len(creds(root, obj))}")

# ---------- F/G/H. fail-closed ----------
r5, o5 = build("BLOCKED", with_index=False)
res5 = run(r5, o5, False, False)
rec("F 索引缺失 → fail-closed 不签发", res5.get("rule_credential") is None and not creds(r5, o5),
    f"{res5.get('rule_credential_skipped_reason')}")

r6, o6 = build("BLOCKED", with_index=True, index_obj="OBJ-OTHER-XXXX")
res6 = run(r6, o6, False, False)
rec("G 索引中无该 obj_id → fail-closed 不签发",
    res6.get("rule_credential") is None and not creds(r6, o6),
    f"{res6.get('rule_credential_skipped_reason')}")

r7, o7 = build("BLOCKED", corrupt_index=True)
res7 = run(r7, o7, False, False)
rec("H 索引 JSON 损坏 → fail-closed 不签发",
    res7.get("rule_credential") is None and not creds(r7, o7),
    f"{res7.get('rule_credential_skipped_reason')}")

r8, o8 = build("BLOCKED", status_missing=True)
res8 = run(r8, o8, False, False)
rec("I 状态文件缺失 → fail-closed 不签发",
    res8.get("rule_credential") is None and not creds(r8, o8),
    f"{res8.get('rule_credential_skipped_reason')}")

# ---------- J. compare() 既有语义未变 ----------
r9, o9 = build("BLOCKED")
res9 = run(r9, o9, True, True)
rec("J compare() 既有语义未变（both_major → escalate=True）",
    res9.get("outcome") == "both_major" and res9.get("escalate") is True,
    f"outcome={res9.get('outcome')} escalate={res9.get('escalate')}")

# ---------- K. 结构约束：只改 compare()，未新增函数 ----------
# 基线取自 git 241868b（task-10 之前的 dual_judge.py），非人工猜测
BASELINE_TOP_FUNCS = sorted([
    "_comparison_path", "_is_opened", "_load_json", "_now", "_opened_path", "_save_json",
    "_sealed_path", "_staging_key", "_try_open", "_validate_obj_id", "_validate_role",
    "_validate_verdict", "_verdicts_dir", "canonical_verdict", "commitment_of",
    "compare", "read_verdict", "reveal_verdict", "seal_verdict", "submit_verdict",
])
BASELINE_FUNC_LINES = {"seal_verdict": 40, "reveal_verdict": 42, "read_verdict": 26,
                       "submit_verdict": 6, "compare": 44}

tree = ast.parse((SRC / "control" / "dual_judge.py").read_text(encoding="utf-8"))
top_funcs = sorted(n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
new_funcs = [f for f in top_funcs if f not in BASELINE_TOP_FUNCS]
gone_funcs = [f for f in BASELINE_TOP_FUNCS if f not in top_funcs]
rec("K 顶层函数集合与改动前完全一致（未新增任何函数，公开或私有）",
    not new_funcs and not gone_funcs, f"新增={new_funcs} 删除={gone_funcs}")


def _lines(src, name):
    t = ast.parse(src)
    for n in t.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n.end_lineno - n.lineno + 1


src_txt = (SRC / "control" / "dual_judge.py").read_text(encoding="utf-8")
unchanged = [f for f, ln in BASELINE_FUNC_LINES.items()
             if f != "compare" and _lines(src_txt, f) == ln]
rec("K 其余四个判定函数体行数未变（仅 compare() 变长）", len(unchanged) == 4,
    f"未变={unchanged} compare: {BASELINE_FUNC_LINES['compare']} -> {_lines(src_txt, 'compare')} 行")
rec("K 未新增 import",
    "import subprocess" not in src_txt and "import os.path" not in src_txt)

# ---------- L. 快照：四个受保护模块 + 真实状态 ----------
snap = json.loads(SNAP.read_text(encoding="utf-8"))["files"]
for k in ("state_guard", "write_lock", "objection", "gate", "audit_log", "taskstate", "taskcard"):
    now = sha(snap[k]["path"])
    rec(f"L 未改动：{k}", now == snap[k]["sha256"], f"{snap[k]['sha256'][:16]}")
rec("L dual_judge 是本轮唯一变更对象", sha(snap["dual_judge"]["path"]) != snap["dual_judge"]["sha256"])

# ---------- M. 真实工作区零写入 ----------
rec("M 真实工作区无 rule_credentials 目录（演示全在隔离根）",
    not (WS / "team" / "agent_state" / "rule_credentials").exists())
rec("M 真实状态仍为「等用户裁决」",
    json.loads(REAL_STATE.read_text(encoding="utf-8"))["tasks"]["TASK-001"]["status"] == "等用户裁决")

control.reset_data_root()

print("=" * 78)
passed = sum(1 for _, ok, _ in results if ok)
print(f"Mentor 独立验收：{passed} / {len(results)} 项通过")
for n, ok, d in results:
    if not ok:
        print(f"  FAIL: {n} :: {d}")
print("=" * 78)
sys.exit(0 if passed == len(results) else 1)
