"""task-10 · 规则凭证演示（compare() 内签发一次性规则凭证）。

七个 SECTION（真实输出）：

* **S1** 隔离根 · ``both_minor`` + 任务非常用户裁决 → **签发**（打印凭证全文与 ``cred_id``）
* **S2** 隔离根 · ``both_major`` → **不签发**（凭证目录清单为空）
* **S3** 隔离根 · ``divergent`` → **不签发**
* **S4** **真实状态值**（``TASK-001`` = 「等用户裁决」）+ ``both_minor`` → **不签发**
* **S5** 幂等：对 S1 的同一 ``obj_id`` 再次 ``compare`` → 不重复签发（凭证数仍为 1）
* **S6** fail-closed：索引缺失 / 查不到 ``obj_id`` → **不签发**
* **S7** 哈希核对：``dual_judge.py`` 已改；``state_guard`` / ``write_lock`` / ``objection`` / ``gate`` 与快照一致

⚠ S4 的取材说明（**已在报告中登记为偏差**）
------------------------------------------------
S4 用的是**真实治理文件的字节级副本**（``task_state.json`` 与 ``objection_index.json``
原样复制到隔离根，哈希与真实文件逐一相等），而**不是**在真实目录就地对真实异议运行 compare。
原因：就地运行必须为真实异议 ``OBJ-20260930-002`` 写入 mentor/controller 判定文件，
那会**凭空造出一份真实的 §8.5 判定记录**（``both_minor`` 会被读成"该异议已按规则封闭"），
而该异议实际上仍在等待 Mentor 书面回应——那属于伪造治理记录，我不做。
副本方案跑的是**同一条代码路径**、读的是**同一个状态值**，结论等价，且对真实工作区零写入。

用法::

    <python> E:\\Socratic_M\\team\\tests\\demo_rule_credential.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

TEAM_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = TEAM_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from control import data_root, dual_judge, objection  # noqa: E402

SNAPSHOT = TEAM_DIR / "evidence" / "snapshot-before-rule-credential.json"
REAL_TASK_STATE = TEAM_DIR / "agent_state" / "task_state.json"
REAL_INDEX = TEAM_DIR / "agent_state" / "objection_index.json"
REAL_VERDICTS = TEAM_DIR / "agent_state" / "verdicts"

MINOR = {"major": False, "hit_rules": [], "scope": "双方均判不重大"}
MAJOR = {"major": True, "hit_rules": [1], "scope": "双方均判重大"}
DIVERGENT_M = {"major": True, "hit_rules": [4], "scope": "Mentor 判重大"}
DIVERGENT_C = {"major": False, "hit_rules": [], "scope": "Controller 判不重大"}


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def creds(root: Path) -> list:
    directory = root / "agent_state" / "rule_credentials"
    return sorted(p.name for p in directory.glob("*.json")) if directory.is_dir() else []


def use_root(root: Path) -> None:
    os.environ["CONTROL_DATA_ROOT"] = str(root)


def fresh_root(tag: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix=f"rc-demo-{tag}-"))
    use_root(root)
    return root


def open_pair(obj_id: str, mentor: dict, controller: dict) -> None:
    dual_judge.seal_verdict("mentor", obj_id, mentor)
    dual_judge.seal_verdict("controller", obj_id, controller)
    dual_judge.reveal_verdict("mentor", obj_id, mentor)
    dual_judge.reveal_verdict("controller", obj_id, controller)


def new_objection() -> str:
    """在**当前数据根**登记一条异议（同时建立 obj_id → task_id 与任务状态记录）。"""
    return objection.submit("TASK-001", "v2", "premise", "演示用异议（task-10）",
                            "见 TASK-001-rule-credential.log", "不主张任何失效")["obj_id"]


def s1() -> tuple:
    print("=" * 78)
    print("S1 · 隔离根 · both_minor + 任务非常用户裁决 → 签发")
    print("=" * 78)
    root = fresh_root("s1")
    obj_id = new_objection()
    open_pair(obj_id, MINOR, MINOR)
    print("数据根          :", root)
    print("obj_id          :", obj_id)
    print("任务状态        :", json.loads((root / "agent_state" / "task_state.json")
                                        .read_text(encoding="utf-8"))["tasks"]["TASK-001"]["status"])
    result = dual_judge.compare(obj_id)
    print("outcome/escalate:", result["outcome"], "/", result["escalate"])
    print("cred_id         :", result["cred_id"])
    print("凭证目录清单    :", creds(root))
    issued = result["rule_credential"]
    if issued:
        print("--- 凭证全文 ---")
        print(json.dumps(issued, ensure_ascii=False, indent=2))
    print("判定            :", "PASS —— 已签发" if issued else "FAIL —— 未签发")
    print()
    return issued is not None, obj_id, root


def s2() -> bool:
    print("=" * 78)
    print("S2 · 隔离根 · both_major → 不签发")
    print("=" * 78)
    root = fresh_root("s2")
    obj_id = new_objection()
    open_pair(obj_id, MAJOR, MAJOR)
    result = dual_judge.compare(obj_id)
    print("outcome/escalate:", result["outcome"], "/", result["escalate"])
    print("rule_credential :", result["rule_credential"])
    print("跳过原因        :", result["rule_credential_skipped_reason"])
    print("凭证目录清单    :", creds(root), "（应为空）")
    ok = result["rule_credential"] is None and creds(root) == []
    print("判定            :", "PASS —— 未签发" if ok else "FAIL")
    print()
    return ok


def s3() -> bool:
    print("=" * 78)
    print("S3 · 隔离根 · divergent → 不签发")
    print("=" * 78)
    root = fresh_root("s3")
    obj_id = new_objection()
    open_pair(obj_id, DIVERGENT_M, DIVERGENT_C)
    result = dual_judge.compare(obj_id)
    print("outcome/escalate:", result["outcome"], "/", result["escalate"])
    print("rule_credential :", result["rule_credential"])
    print("跳过原因        :", result["rule_credential_skipped_reason"])
    print("凭证目录清单    :", creds(root), "（应为空）")
    ok = result["rule_credential"] is None and creds(root) == []
    print("判定            :", "PASS —— 未签发" if ok else "FAIL")
    print()
    return ok


def s4() -> bool:
    print("=" * 78)
    print("S4 · 真实状态值（TASK-001 = 等用户裁决）+ both_minor → 不签发")
    print("=" * 78)
    real_state_before = sha256_of(REAL_TASK_STATE)
    real_index_before = sha256_of(REAL_INDEX)
    real_verdicts_before = sorted(p.name for p in REAL_VERDICTS.glob("*")) if REAL_VERDICTS.is_dir() else []

    print("真实 task_state.json :", " ".join(REAL_TASK_STATE.read_text(encoding="utf-8").split()))
    print("真实状态 SHA256      :", real_state_before)
    real_index = json.loads(REAL_INDEX.read_text(encoding="utf-8"))
    entry = [e for e in real_index.get("objections", []) if isinstance(e, dict)][-1]
    print("真实索引末条         : obj_id=", entry.get("obj_id"), " task_id=", entry.get("task_id"))

    root = fresh_root("s4")
    (root / "agent_state").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REAL_TASK_STATE, root / "agent_state" / "task_state.json")
    shutil.copyfile(REAL_INDEX, root / "agent_state" / "objection_index.json")
    state_copy = root / "agent_state" / "task_state.json"
    index_copy = root / "agent_state" / "objection_index.json"
    print("副本（隔离根）       :", root)
    print("副本状态哈希         :", sha256_of(state_copy), "| 与真实一致:",
          sha256_of(state_copy) == real_state_before)
    print("副本索引哈希         :", sha256_of(index_copy), "| 与真实一致:",
          sha256_of(index_copy) == real_index_before)

    obj_id = entry["obj_id"]
    open_pair(obj_id, MINOR, MINOR)
    result = dual_judge.compare(obj_id)
    print("obj_id               :", obj_id, "→ 真实 task_id:", entry.get("task_id"))
    print("读到的任务状态       :", json.loads(state_copy.read_text(encoding='utf-8'))["tasks"][entry["task_id"]]["status"])
    print("outcome/escalate     :", result["outcome"], "/", result["escalate"])
    print("rule_credential      :", result["rule_credential"])
    print("跳过原因             :", result["rule_credential_skipped_reason"])
    print("凭证目录清单         :", creds(root), "（应为空）")

    real_state_after = sha256_of(REAL_TASK_STATE)
    real_index_after = sha256_of(REAL_INDEX)
    real_verdicts_after = sorted(p.name for p in REAL_VERDICTS.glob("*")) if REAL_VERDICTS.is_dir() else []
    print("真实状态哈希（后）   :", real_state_after, "| 未变:", real_state_after == real_state_before)
    print("真实索引哈希（后）   :", real_index_after, "| 未变:", real_index_after == real_index_before)
    print("真实 verdicts 目录   : 未新增文件:", real_verdicts_after == real_verdicts_before)

    ok = (result["rule_credential"] is None and creds(root) == []
          and real_state_after == real_state_before and real_index_after == real_index_before
          and real_verdicts_after == real_verdicts_before)
    print("判定            :", "PASS —— 用户裁决中的任务未被规则凭证放行，且真实治理文件零写入" if ok else "FAIL")
    print()
    return ok


def s5(obj_id: str, root: Path) -> bool:
    print("=" * 78)
    print("S5 · 幂等：对 S1 的同一 obj_id 再次 compare → 不重复签发")
    print("=" * 78)
    use_root(root)
    before = creds(root)
    print("再次 compare 前凭证数:", len(before), before)
    result = dual_judge.compare(obj_id)
    after = creds(root)
    print("rule_credential      :", result["rule_credential"])
    print("跳过原因             :", result["rule_credential_skipped_reason"])
    print("再次 compare 后凭证数:", len(after), after)
    ok = result["rule_credential"] is None and len(after) == 1
    print("判定            :", "PASS —— 幂等，凭证数仍为 1" if ok else "FAIL")
    print()
    return ok


def s6() -> bool:
    print("=" * 78)
    print("S6 · fail-closed：索引缺失 / 查不到 obj_id → 不签发")
    print("=" * 78)
    root = fresh_root("s6")
    obj_id = new_objection()
    open_pair(obj_id, MINOR, MINOR)
    (root / "agent_state" / "objection_index.json").unlink()
    print("数据根          :", root)
    print("已删除          : agent_state/objection_index.json")
    result = dual_judge.compare(obj_id)
    print("rule_credential :", result["rule_credential"])
    print("跳过原因        :", result["rule_credential_skipped_reason"])
    print("凭证目录清单    :", creds(root), "（应为空）")
    ok = result["rule_credential"] is None and creds(root) == []
    print("判定            :", "PASS —— fail-closed，未签发" if ok else "FAIL")
    print()
    return ok


def s7() -> bool:
    print("=" * 78)
    print("S7 · 哈希核对（对 evidence/snapshot-before-rule-credential.json）")
    print("=" * 78)
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    protected = {"state_guard", "write_lock", "objection", "gate"}
    ok = True
    for key, entry in snap["files"].items():
        path = Path(entry["path"])
        now = sha256_of(path)
        same = now == entry["sha256"]
        if key == "dual_judge":
            print(("CHANGED" if not same else "MATCH  "), key, "（本次授权变更对象）")
            if not same:
                print("        快照:", entry["sha256"])
                print("        现状:", now)
            continue
        if key in protected:
            ok &= same
            print(("MATCH  " if same else "★CHANGED★"), key, "（受保护模块）")
        else:
            print(("MATCH  " if same else "CHANGED"), key, "（非受保护项）" if same else "（非受保护项·未被本任务要求一致）")
    print("四个受保护模块全部与快照一致:", ok)
    print("判定            :", "PASS" if ok else "FAIL")
    print()
    return ok


def main() -> int:
    print("=== task-10 · compare() 内签发一次性规则凭证（仅 both_minor）===")
    print("解释器版本:", sys.version.split()[0])
    print()

    r1, obj_id, root1 = s1()
    r2 = s2()
    r3 = s3()
    r4 = s4()
    r5 = s5(obj_id, root1)
    r6 = s6()
    r7 = s7()

    print("=" * 78)
    print("总判定")
    print("=" * 78)
    for label, value in (("S1 both_minor → 签发", r1),
                         ("S2 both_major → 不签发", r2),
                         ("S3 divergent → 不签发", r3),
                         ("S4 真实状态「等用户裁决」→ 不签发", r4),
                         ("S5 幂等（凭证数仍为 1）", r5),
                         ("S6 fail-closed → 不签发", r6),
                         ("S7 四个受保护模块与快照一致", r7)):
        print(f"{'PASS' if value else 'FAIL'}  {label}")
    all_pass = all([r1, r2, r3, r4, r5, r6, r7])
    print("总判定:", "PASS" if all_pass else "FAIL")
    print("=" * 78)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
