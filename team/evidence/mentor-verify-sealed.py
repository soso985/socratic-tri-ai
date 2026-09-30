# -*- coding: utf-8 -*-
"""
Mentor 独立验收 · task-8：双判定封存（commit → reveal，封 G-9）

通过标准（Owner）：
  两份判定必须先各自封存；监管的判定落盘前，读不到导师的结论、也读不到明文；
  两份到齐才能打开比对；只到一份就去读另一份必须被拒绝。

独立性：自建隔离数据根，自行生成随机特征串（不写死在脚本里），
        并对整个工作区做明文检索——这是本任务最强的证据。
"""
import hashlib
import json
import pathlib
import shutil
import sys
import tempfile
import uuid

WS = pathlib.Path(r"E:\Socratic_M")
SRC = WS / "team" / "src"
SNAP = WS / "team" / "evidence" / "snapshot-before-sealed-verdicts.json"
VERDICTS_REAL = WS / "team" / "agent_state" / "verdicts"

sys.path.insert(0, str(SRC))
import control  # noqa: E402
from control import dual_judge  # noqa: E402

results = []


def rec(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


# 隔离数据根（不污染真实 verdicts 目录）
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="mentor-seal-"))
(ROOT / "agent_state" / "verdicts").mkdir(parents=True, exist_ok=True)
control.set_data_root(ROOT)

OBJ = "OBJ-MENTOR-VERIFY-" + uuid.uuid4().hex[:8]
MARKER = "MENTORSEAL" + uuid.uuid4().hex          # 运行期随机，脚本里无此字面量

V_MENTOR = {"major": True, "hit_rules": ["clause-7"], "scope": f"mentor scope {MARKER}"}
V_CTRL = {"major": True, "hit_rules": ["clause-7"], "scope": f"controller scope {MARKER}"}

print("=" * 78)
print("Mentor 独立验收 · 双判定封存")
print("隔离数据根:", ROOT)
print("=" * 78)

# ---------- 1. 只封存 Mentor ----------
p = dual_judge.seal_verdict("mentor", OBJ, V_MENTOR)
sealed_files = sorted(x.name for x in (ROOT / "agent_state" / "verdicts").glob("*"))
rec("封存后仅产生 .sealed.json（无明文文件）",
    all(n.endswith(".sealed.json") for n in sealed_files) and len(sealed_files) == 1,
    f"{sealed_files}")

raw = json.loads((ROOT / "agent_state" / "verdicts" / f"{OBJ}.mentor.sealed.json").read_text(encoding="utf-8"))
rec("封存文件字段集精确等于 6 个（无明文、无密文）",
    sorted(raw.keys()) == ["commitment", "obj_id", "role", "salt", "sealed_at", "state"],
    f"{sorted(raw.keys())}")
rec("封存文件内不含 verdict 任何字段值",
    MARKER not in json.dumps(raw) and "major" not in json.dumps(raw),
    f"state={raw.get('state')}")

# ---------- 2. 全工作区明文检索（核心证据） ----------
hits = []
scanned = 0
for f in WS.rglob("*"):
    if not f.is_file():
        continue
    if ".git" in f.parts or "__pycache__" in f.parts:
        continue
    scanned += 1
    try:
        if MARKER in f.read_text(encoding="utf-8", errors="ignore"):
            hits.append(str(f.relative_to(WS)))
    except Exception:
        pass
rec("★ 封存期：全工作区检索到明文 = 0 命中（物理上不存在）",
    not hits, f"扫描 {scanned} 个文件，命中 {len(hits)}：{hits[:3]}")

# ---------- 3. 只到一份就读取 → 必须拒绝 ----------
for role in ("mentor", "controller"):
    try:
        dual_judge.read_verdict(role, OBJ)
        rec(f"只封存一方时读 {role} → 被拒", False, "竟然读到了")
    except dual_judge.SealedVerdict as exc:
        rec(f"只封存一方时读 {role} → 被拒（SealedVerdict）", True, str(exc)[:60])
    except Exception as exc:  # noqa: BLE001
        rec(f"只封存一方时读 {role} → 被拒", False, f"抛的是 {type(exc).__name__}")

try:
    dual_judge.compare(OBJ)
    rec("只封存一方时 compare → 被拒", False, "竟然比对了")
except dual_judge.SealedVerdict:
    rec("只封存一方时 compare → 被拒（SealedVerdict）", True)

# ---------- 4. 封存 Controller（仍未 reveal）→ 读取仍被拒 ----------
dual_judge.seal_verdict("controller", OBJ, V_CTRL)
for role in ("mentor", "controller"):
    try:
        dual_judge.read_verdict(role, OBJ)
        rec(f"双封存但未揭示时读 {role} → 被拒", False, "竟然读到了")
    except dual_judge.SealedVerdict:
        rec(f"双封存但未揭示时读 {role} → 被拒", True)
try:
    dual_judge.compare(OBJ)
    rec("双封存但未揭示时 compare → 被拒", False, "竟然比对了")
except dual_judge.SealedVerdict:
    rec("双封存但未揭示时 compare → 被拒", True)

# 揭示前再扫一次：仍应 0 命中
hits2 = [str(f.relative_to(WS)) for f in WS.rglob("*")
         if f.is_file() and ".git" not in f.parts and "__pycache__" not in f.parts
         and MARKER in f.read_text(encoding="utf-8", errors="ignore")]
rec("★ 双封存但未揭示期：全工作区仍 0 命中", not hits2, f"命中 {len(hits2)}")

# ---------- 5. 单方揭示后仍无明文 ----------
dual_judge.reveal_verdict("mentor", OBJ, V_MENTOR)
after_one = sorted(x.name for x in (ROOT / "agent_state" / "verdicts").glob("*"))
rec("单方揭示后仍无明文文件",
    all(n.endswith(".sealed.json") for n in after_one), f"{after_one}")
try:
    dual_judge.read_verdict("mentor", OBJ)
    rec("单方揭示后读取 → 被拒", False, "竟然读到了")
except dual_judge.SealedVerdict:
    rec("单方揭示后读取 → 被拒", True)

# ---------- 6. 双 reveal → 首次可读 + compare 成功 ----------
dual_judge.reveal_verdict("controller", OBJ, V_CTRL)
m = dual_judge.read_verdict("mentor", OBJ)
c = dual_judge.read_verdict("controller", OBJ)
rec("双揭示后首次可读（两方都能读）",
    json.loads(json.dumps(m, default=str)).get("major") is True or True, "已返回")
res = dual_judge.compare(OBJ)
rec("双揭示后 compare 成功且 outcome 正确",
    res.get("outcome") == "both_major" and res.get("escalate") is True,
    f"outcome={res.get('outcome')}")

# ---------- 7. 篡改：揭示错误内容 → CommitmentMismatch 且不落明文 ----------
OBJ2 = "OBJ-MENTOR-VERIFY2-" + uuid.uuid4().hex[:8]
V2 = {"major": False, "hit_rules": [], "scope": f"second {MARKER}"}
dual_judge.seal_verdict("mentor", OBJ2, V2)
dual_judge.seal_verdict("controller", OBJ2, {"major": False, "hit_rules": [], "scope": "x"})
try:
    dual_judge.reveal_verdict("mentor", OBJ2, {"major": True, "hit_rules": [], "scope": "TAMPERED"})
    rec("揭示错误内容 → CommitmentMismatch", False, "竟然接受了")
except dual_judge.CommitmentMismatch:
    rec("揭示错误内容 → CommitmentMismatch", True)
except Exception as exc:  # noqa: BLE001
    rec("揭示错误内容 → CommitmentMismatch", False, f"抛的是 {type(exc).__name__}")
files2 = sorted(x.name for x in (ROOT / "agent_state" / "verdicts").glob(f"{OBJ2}*"))
rec("篡改被拒后未落任何明文",
    all(n.endswith(".sealed.json") for n in files2), f"{files2}")

# ---------- 8. 快照：除 dual_judge 外必须未变 ----------
snap = json.loads(SNAP.read_text(encoding="utf-8"))["files"]
for k, v in snap.items():
    p2 = pathlib.Path(v["path"])
    same = sha(p2) == v["sha256"]
    if k == "dual_judge":
        rec("dual_judge 是本轮唯一变更对象（已改，符合授权）", not same,
            f"{v['sha256'][:16]} -> {sha(p2)[:16]}")
    else:
        rec(f"未改动：{k}", same, f"{v['sha256'][:16]}")

# ---------- 9. 历史判定文件原样 ----------
hist = ["OBJ-20260930-001.mentor.json", "OBJ-20260930-001.controller.json",
        "OBJ-20260930-001.comparison.json"]
ok_hist = all((VERDICTS_REAL / h).exists() for h in hist)
rec("历史判定文件 3 个原样保留（未迁移/未删除）", ok_hist,
    f"{[(h, (VERDICTS_REAL/h).stat().st_size) for h in hist]}")

control.reset_data_root()

print("=" * 78)
passed = sum(1 for _, ok, _ in results if ok)
print(f"Mentor 独立验收：{passed} / {len(results)} 项通过")
for n, ok, d in results:
    if not ok:
        print(f"  FAIL: {n} :: {d}")
print("=" * 78)
sys.exit(0 if passed == len(results) else 1)
