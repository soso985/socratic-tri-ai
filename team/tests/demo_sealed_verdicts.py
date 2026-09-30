"""task-8 · 双判定封存演示（commit → reveal）：封存期内磁盘上没有明文。

**本脚本只做验证，不改任何模块**——`dual_judge.py` 已由本次任务改为封存协议。

五个 SECTION：
* **S1** 只封存 Mentor → ``read_verdict`` 必须抛 ``SealedVerdict``；
  并**实证磁盘上没有明文**：打印 verdicts 目录清单 +
  对判决内容里的**特征串做全目录（整个工作区）检索 → 必须 0 命中**。
* **S2** 再封存 Controller（未 reveal）→ 读取仍被拒。
* **S3** 双份 reveal → **首次可读** → ``compare`` 成功且与预期 outcome 一致。
* **S4** 只封存一方就 ``compare`` → 必须抛 ``SealedVerdict``。
* **S5** reveal 时故意给错 verdict → ``CommitmentMismatch``，且**不得落明文**。

运行在**真实数据根**上（否则"封存期内磁盘无明文"会因什么都没写而变得没有意义），
对象 ID 与特征串**每次运行都唯一**，以免与历史记录或上次运行相互干扰。

用法::

    <python> E:\\Socratic_M\\team\\tests\\demo_sealed_verdicts.py
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sys
import uuid
from pathlib import Path

TEAM_DIR = Path(__file__).resolve().parents[1]              # <工作区>/team
SRC_DIR = TEAM_DIR / "src"
WORKSPACE = TEAM_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from control import data_root, dual_judge  # noqa: E402
from control.dual_judge import (  # noqa: E402
    CommitmentMismatch,
    SealedVerdict,
    compare,
    read_verdict,
    reveal_verdict,
    seal_verdict,
)

RUN = secrets.token_hex(4)
OBJ_A = f"OBJ-DEMO-TASK8-{RUN}-A"      # S1–S3
OBJ_B = f"OBJ-DEMO-TASK8-{RUN}-B"      # S4
OBJ_C = f"OBJ-DEMO-TASK8-{RUN}-C"      # S5

#: 判决内容里的特征串 —— 若它能在工作区任何文件里被检索到，就说明封存失效
MARKER = f"MENTOR-SEALED-MARKER-{uuid.uuid4().hex}"

MENTOR_VERDICT = {"major": True, "hit_rules": [1], "scope": f"L0 需求相关 · {MARKER}"}
CONTROLLER_VERDICT = {"major": True, "hit_rules": [1], "scope": "L0 需求相关"}


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verdicts_dir() -> Path:
    return data_root() / "agent_state" / "verdicts"


def listing() -> list:
    return sorted(p.name for p in verdicts_dir().glob("*") if p.is_file())


def search_workspace(needle: str):
    """在整个工作区里检索特征串（跳过 .git：版本库内部对象，非记录落点）。"""
    hits = []
    scanned = 0
    target = needle.encode("utf-8")
    for path in sorted(WORKSPACE.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        scanned += 1
        try:
            if target in path.read_bytes():
                hits.append(str(path.relative_to(WORKSPACE)))
        except OSError:
            continue
    return hits, scanned


def try_read(label: str, role: str, obj_id: str) -> bool:
    """尝试读取；返回是否**被拒**（被拒 = 符合预期）。"""
    try:
        got = read_verdict(role, obj_id)
        print(f"  {label}: !!! 竟然读到了明文 !!! {json.dumps(got['verdict'], ensure_ascii=False)}")
        return False
    except SealedVerdict as exc:
        print(f"  {label}: 抛出 SealedVerdict（拒绝读取）")
        print(f"      {exc}")
        return True


def try_compare(label: str, obj_id: str) -> bool:
    try:
        got = compare(obj_id)
        print(f"  {label}: !!! 竟然比对成功 !!! {got}")
        return False
    except SealedVerdict as exc:
        print(f"  {label}: 抛出 SealedVerdict（拒绝比对）")
        print(f"      {exc}")
        return True


def section1() -> bool:
    print("=" * 78)
    print("S1 · 只封存 Mentor → 读取必须被拒 + 全目录检索明文特征串 → 0 命中")
    print("=" * 78)
    print("数据根        :", data_root())
    print("verdicts 目录 :", verdicts_dir())
    print("对象 ID       :", OBJ_A)
    print()

    result = seal_verdict("mentor", OBJ_A, MENTOR_VERDICT)
    sealed_path = Path(result["sealed_path"])
    print("--- 封存动作 ---")
    print("commitment    :", result["commitment"])
    print("salt          :", result["salt"])
    print("封存文件路径  :", sealed_path)
    print("封存文件内容  :", " ".join(sealed_path.read_text(encoding="utf-8").split()))
    print("封存文件字段  :", sorted(json.loads(sealed_path.read_text(encoding="utf-8"))))
    print()

    print("--- 第 1 步：此时尝试读取 ---")
    denied_mentor = try_read('read_verdict("mentor", ...)', "mentor", OBJ_A)
    denied_controller = try_read('read_verdict("controller", ...)', "controller", OBJ_A)
    print()

    print("--- 第 2 步：磁盘上到底有没有明文？（本任务最有价值的证据）---")
    print("verdicts 目录清单:")
    for name in listing():
        print("   ", name)
    plaintext_files = [n for n in listing() if n.startswith(OBJ_A) and n.endswith((".mentor.json", ".controller.json"))]
    print("属于本次对象 ID 的明文文件:", plaintext_files or "（无）")
    hits, scanned = search_workspace(MARKER)
    print(f"特征串           : {MARKER}")
    print(f"检索范围         : {WORKSPACE}（跳过 .git）")
    print(f"已扫描文件数     : {scanned}")
    print(f"命中数           : {len(hits)}")
    for hit in hits:
        print("    命中:", hit)
    print("判定             :", "PASS —— 封存期内磁盘上不存在明文（物理事实）" if not hits else "FAIL —— 明文泄漏")
    print(f"（说明：上面这行特征串是在检索**之后**才打印的；检索时刻它只存在于本进程内存中。）")
    print()
    return denied_mentor and denied_controller and not hits


def section2() -> bool:
    print("=" * 78)
    print("S2 · 再封存 Controller（仍未 reveal）→ 读取仍被拒")
    print("=" * 78)
    result = seal_verdict("controller", OBJ_A, CONTROLLER_VERDICT)
    print("Controller commitment:", result["commitment"])
    print("封存文件字段        :", sorted(json.loads(Path(result["sealed_path"]).read_text(encoding="utf-8"))))
    print("--- 双份封存齐备、但双方都未揭示 ---")
    ok1 = try_read('read_verdict("mentor", ...)', "mentor", OBJ_A)
    ok2 = try_read('read_verdict("controller", ...)', "controller", OBJ_A)
    ok3 = try_compare("compare(...)", OBJ_A)
    print("判定             :", "PASS —— 未揭示前依然读不到" if (ok1 and ok2 and ok3) else "FAIL")
    print()
    return ok1 and ok2 and ok3


def section3() -> bool:
    print("=" * 78)
    print("S3 · 双份 reveal → 首次可读 → compare 成功")
    print("=" * 78)
    r1 = reveal_verdict("mentor", OBJ_A, MENTOR_VERDICT)
    print("揭示 mentor      :", json.dumps(r1, ensure_ascii=False))
    print("（此时仅一方揭示 → 仍不应有明文）")
    plaintext_now = [n for n in listing() if n.startswith(OBJ_A) and n.endswith(tuple(f".{r}.json" for r in dual_judge.ROLES))]
    print("明文文件          :", plaintext_now or "（无）")
    ok_sealed = not plaintext_now

    r2 = reveal_verdict("controller", OBJ_A, CONTROLLER_VERDICT)
    print("揭示 controller  :", json.dumps(r2, ensure_ascii=False))
    print()

    print("--- 开封后读取 ---")
    mentor = read_verdict("mentor", OBJ_A)
    controller = read_verdict("controller", OBJ_A)
    print("mentor 明文      :", json.dumps(mentor["verdict"], ensure_ascii=False))
    print("controller 明文  :", json.dumps(controller["verdict"], ensure_ascii=False))
    print("开封文件路径     :", verdicts_dir() / f"{OBJ_A}.mentor.json")
    print()
    outcome = compare(OBJ_A)
    print("compare 结果     :", json.dumps(outcome, ensure_ascii=False))
    expected = {"outcome": "both_major", "escalate": True}
    ok_outcome = outcome == expected
    print("与预期一致       :", ok_outcome, f"（预期 {expected}）")
    cmp_path = verdicts_dir() / f"{OBJ_A}.comparison.json"
    print("比对留档         :", cmp_path, "存在 =", cmp_path.exists())
    print("判定             :", "PASS —— 双份揭示后才首次可读并可比对"
          if (ok_sealed and ok_outcome and cmp_path.exists()) else "FAIL")
    print()
    return ok_sealed and ok_outcome and cmp_path.exists()


def section4() -> bool:
    print("=" * 78)
    print("S4 · 只封存一方就 compare → 必须抛 SealedVerdict")
    print("=" * 78)
    seal_verdict("mentor", OBJ_B, {"major": False, "hit_rules": [], "scope": "单方封存"})
    print("对象 ID          :", OBJ_B, "（只封存了 mentor）")
    denied = try_compare("compare(...)", OBJ_B)
    print("判定             :", "PASS —— 单方封存时比对被拒" if denied else "FAIL")
    print()
    return denied


def section5() -> bool:
    print("=" * 78)
    print("S5 · reveal 时故意给错 verdict → CommitmentMismatch 且不落明文")
    print("=" * 78)
    seal_verdict("mentor", OBJ_C, {"major": True, "hit_rules": [1], "scope": "原始判定"})
    seal_verdict("controller", OBJ_C, {"major": False, "hit_rules": [], "scope": "原始判定"})
    print("对象 ID          :", OBJ_C, "（双份已封存）")
    wrong = {"major": False, "hit_rules": [7], "scope": "被篡改的判定"}
    print("揭示时故意给错   :", json.dumps(wrong, ensure_ascii=False))
    mismatched = False
    try:
        reveal_verdict("mentor", OBJ_C, wrong)
        print("  !!! 竟然通过校验 !!!")
    except CommitmentMismatch as exc:
        mismatched = True
        print("  抛出 CommitmentMismatch：")
        print("     ", exc)

    plaintext = [n for n in listing() if n.startswith(OBJ_C) and n.endswith(tuple(f".{r}.json" for r in dual_judge.ROLES))]
    sealed_state = json.loads((verdicts_dir() / f"{OBJ_C}.mentor.sealed.json").read_text(encoding="utf-8"))["state"]
    print("该对象的明文文件 :", plaintext or "（无）")
    print("mentor 封存状态  :", sealed_state)
    print("判定             :", "PASS —— 承诺不符被拒且未落明文"
          if (mismatched and not plaintext and sealed_state == "SEALED") else "FAIL")
    print()
    return mismatched and not plaintext and sealed_state == "SEALED"


def history_check() -> bool:
    """既有历史判定文件必须原样未动（只读比对，不做任何迁移/删除/改写）。"""
    print("=" * 78)
    print("既有历史判定文件检查（OBJ-20260930-001.*，只读）")
    print("=" * 78)
    ok = True
    for name in ("OBJ-20260930-001.mentor.json",
                 "OBJ-20260930-001.controller.json",
                 "OBJ-20260930-001.comparison.json"):
        path = verdicts_dir() / name
        exists = path.exists()
        print(f"{'存在' if exists else '缺失'}  {name}")
        if exists:
            print(f"        SHA256: {sha256_of(path)}")
            # 旧格式：直接落明文、没有 .sealed.json；新语义下应无法比对
        ok &= exists
    try:
        compare("OBJ-20260930-001")
        print("compare('OBJ-20260930-001') : !!! 竟然成功 —— 不应发生")
        ok = False
    except SealedVerdict:
        print("compare('OBJ-20260930-001') : 抛出 SealedVerdict（预期：旧记录无封存，无法再比对）")
    print("判定             :", "PASS —— 历史文件原样保留，未被迁移/删除/改写" if ok else "FAIL")
    print()
    return ok


def main() -> int:
    print("=== task-8 · 双判定封存演示（commit → reveal）===")
    print("解释器版本:", sys.version.split()[0])
    print("运行标识    :", RUN)
    print()

    s1 = section1()
    s2 = section2()
    s3 = section3()
    s4 = section4()
    s5 = section5()
    hist = history_check()

    print("=" * 78)
    print("总判定")
    print("=" * 78)
    for label, value in (("S1 单方封存：读取被拒 + 全目录 0 命中", s1),
                         ("S2 双份封存未揭示：读取仍被拒", s2),
                         ("S3 双份揭示：首次可读 + compare 成功", s3),
                         ("S4 单方封存：compare 被拒", s4),
                         ("S5 错误揭示：CommitmentMismatch 且无明文", s5),
                         ("历史判定文件原样保留", hist)):
        print(f"{'PASS' if value else 'FAIL'}  {label}")
    all_pass = all([s1, s2, s3, s4, s5, hist])
    print("总判定:", "PASS" if all_pass else "FAIL")
    print("=" * 78)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
