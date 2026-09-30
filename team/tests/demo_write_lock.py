"""写文件锁 · 状态源演示（task-6 核心交付 · Owner 直接指令）。

用法（唯一解释器）::

    <python> E:\\Socratic_M\\team\\tests\\demo_write_lock.py

两段演示（都是真实运行）：

**第一段 · 真实工作区（撒谎用例）**
权威文件 ``team/agent_state/task_state.json`` 里 ``tasks.TASK-001.status = BLOCKED``（非 RUNNING）。
选 ``team/src/`` 下一个**已有文件**，调用方**故意传** ``{"status": "RUNNING"}`` 去改它：
必须抛 ``WriteDenied``，目标**前后 SHA256 完全相同**，且审计日志记下拒绝。

**第二段 · 隔离数据根（放行用例）**
在临时目录里造一份 ``status: "RUNNING"`` 的 ``task_state.json``，
同一把锁必须**放行**写入——证明它不是"一律拒绝"。

本脚本**只读**既有文件；唯一的写入是：第一段由程序自身追加的那条拒绝记录（豁免于本锁），
以及第二段在临时目录里的文件。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

TEAM_DIR = Path(__file__).resolve().parents[1]          # <工作区>/team
SRC_DIR = TEAM_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from control import data_root  # noqa: E402
from control.write_lock import (  # noqa: E402
    WriteDenied,
    locked_write,
    read_authoritative_status,
    task_state_path,
)

#: 篡改目标：team/src/ 下一个**既有**文件
TARGET = SRC_DIR / "control" / "gate.py"

#: 演示选择的记录（选择器，不是状态断言）
DEMO_TASK_ID = "TASK-001"

#: 调用方**故意撒谎**：权威文件里该任务是 BLOCKED，这里却声称 RUNNING
LYING_CALLER_STATE = {"status": "RUNNING"}


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_src() -> dict:
    return {
        str(p.relative_to(SRC_DIR)).replace("\\", "/"): sha256_of(p)
        for p in sorted(SRC_DIR.rglob("*.py"))
    }


def audit_records() -> list:
    log = data_root() / "agent_state" / "audit_log.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def section_real_workspace() -> bool:
    print("=" * 78)
    print("第一段 · 真实工作区（撒谎用例：调用方声称 RUNNING，权威文件是 BLOCKED）")
    print("=" * 78)
    print("数据根            :", data_root())
    print("权威状态文件      :", task_state_path())

    # 只读权威文件，打印其真实状态（证明演示没有篡改它）
    authoritative_raw = task_state_path().read_text(encoding="utf-8")
    try:
        authoritative_status = read_authoritative_status(DEMO_TASK_ID)
    except Exception as exc:  # noqa: BLE001
        authoritative_status = f"<读取失败：{exc}>"
    print("权威文件原文      :", " ".join(authoritative_raw.split()))
    print("权威状态（只读）  :", authoritative_status)
    print("篡改目标          :", TARGET)
    print("调用方传入        :", json.dumps(LYING_CALLER_STATE, ensure_ascii=False), "  ← 故意撒谎")
    print()

    before_hash = sha256_of(TARGET)
    before_mtime = TARGET.stat().st_mtime_ns
    before_size = TARGET.stat().st_size
    before_authoritative = sha256_of(task_state_path())
    src_before = snapshot_src()
    records_before = audit_records()

    print("--- 演示前 ---")
    print("目标 SHA256       :", before_hash)
    print("目标 mtime_ns     :", before_mtime)
    print("目标 字节数       :", before_size)
    print("权威文件 SHA256   :", before_authoritative)
    print("审计日志条目数    :", len(records_before))
    print()

    print("--- 尝试写入（调用方声称 RUNNING）---")
    print(f"调用：locked_write({str(TARGET)!r}, '<篡改内容>', task_id={DEMO_TASK_ID!r}, "
          f"task_state={LYING_CALLER_STATE!r})")
    raised = None
    try:
        locked_write(TARGET, "# 本行若出现即表示锁失效 —— 篡改成功\n",
                     task_id=DEMO_TASK_ID, task_state=LYING_CALLER_STATE)
        print("结果：!!! 未抛出异常 —— 锁失效，撒谎成功了 !!!")
    except WriteDenied as exc:
        raised = exc
        print("结果：抛出 WriteDenied（撒谎被拒）")
        print("异常信息          :", exc)
        print("权威读到 state    :", exc.state)
        print("state_source      :", exc.state_source)
        print("拒绝记录 audit    :", exc.audit_hash)
        print("留痕失败 audit_err:", exc.audit_error)
    print()

    after_hash = sha256_of(TARGET)
    after_mtime = TARGET.stat().st_mtime_ns
    after_size = TARGET.stat().st_size
    after_authoritative = sha256_of(task_state_path())
    src_after = snapshot_src()
    records_after = audit_records()

    print("--- 演示后 ---")
    print("目标 SHA256       :", after_hash)
    print("目标 mtime_ns     :", after_mtime)
    print("目标 字节数       :", after_size)
    print("权威文件 SHA256   :", after_authoritative)
    print("审计日志条目数    :", len(records_after))
    print()

    print("--- 判定（通过标准：前后 SHA256 完全相同）---")
    same_hash = before_hash == after_hash
    print("前后 SHA256 相同  :", same_hash)
    print("前后 mtime 相同   :", before_mtime == after_mtime)
    print("前后字节数相同    :", before_size == after_size)
    print("抛出 WriteDenied  :", raised is not None)
    print("权威文件未被篡改  :", before_authoritative == after_authoritative)
    print()

    print("--- 审计日志新增条目（拒绝记录，豁免于本锁）---")
    new_records = records_after[len(records_before):]
    if not new_records:
        print("（无新增条目 —— 拒绝未被留痕）")
    for record in new_records:
        print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    print()

    print("--- team/src/ 既有文件快照比对（演示前 → 演示后）---")
    all_unchanged = True
    for name in sorted(set(src_before) | set(src_after)):
        same = src_before.get(name) == src_after.get(name)
        all_unchanged &= same
        print(("相同   " if same else "★已改变★"), name)
        if not same:
            print("        前:", src_before.get(name))
            print("        后:", src_after.get(name))
    print("src/ 全部既有文件未被改动:", all_unchanged)
    print()

    section_pass = same_hash and raised is not None and all_unchanged and \
        before_authoritative == after_authoritative
    print("第一段结论:", "PASS —— 撒谎调用被真实拦下，目标与权威文件均一个字节未变"
          if section_pass else "FAIL")
    print()
    return section_pass


def section_isolated_running() -> bool:
    print("=" * 78)
    print("第二段 · 隔离数据根（放行用例：权威状态 = RUNNING）")
    print("=" * 78)

    sandbox = Path(tempfile.mkdtemp(prefix="writelock-demo-"))
    (sandbox / "agent_state").mkdir(parents=True, exist_ok=True)
    (sandbox / "agent_state" / "task_state.json").write_text(
        json.dumps({"schema": "task-internal/v1", "tasks": {DEMO_TASK_ID: {"status": "RUNNING"}}},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.environ["CONTROL_DATA_ROOT"] = str(sandbox)   # 只影响本进程；不碰真实工作区

    target = sandbox / "output" / "allowed.txt"
    print("隔离数据根        :", sandbox)
    print("权威状态文件      :", task_state_path())
    print("权威状态（只读）  :", read_authoritative_status(DEMO_TASK_ID))
    print("目标（隔离目录内）:", target)

    result = None
    error = None
    try:
        result = locked_write(target, "写入被允许：权威状态为 RUNNING\n",
                              task_id=DEMO_TASK_ID, task_state={"status": "BLOCKED"})
    except WriteDenied as exc:
        error = exc
    print("调用：locked_write(<隔离目标>, '<内容>', task_id='TASK-001', "
          "task_state={'status': 'BLOCKED'})   ← 调用方这次反向撒谎")
    if error is not None:
        print("结果：被拒绝（不符合预期）：", error)
        return False
    print("结果：放行 ✔")
    print("返回              :", json.dumps(result, ensure_ascii=False))
    print("目标是否存在      :", target.exists())
    print("目标内容          :", repr(target.read_text(encoding="utf-8")))
    section_pass = target.exists() and target.read_text(encoding="utf-8").startswith("写入被允许")
    print("第二段结论:", "PASS —— 权威为 RUNNING 时确实放行（锁不是一律拒绝）"
          if section_pass else "FAIL")
    print()
    return section_pass


def main() -> int:
    print("=== TASK-001 写文件锁 · 状态源演示（状态只认权威文件）===")
    print("解释器版本:", sys.version.split()[0])
    print()

    # 第一段按任务要求**必须**跑在真实工作区上：临时移除可能继承来的重定向
    inherited = os.environ.pop("CONTROL_DATA_ROOT", None)
    if inherited:
        print(f"⚠ 环境变量 CONTROL_DATA_ROOT 原为 {inherited!r} —— "
              "第一段要求对真实工作区演示，已临时移除该重定向")
        print()

    real_ok = section_real_workspace()
    isolated_ok = section_isolated_running()

    print("=" * 78)
    print(f"总判定: 第一段(真实工作区·撒谎被拒)={'PASS' if real_ok else 'FAIL'} | "
          f"第二段(隔离·RUNNING 放行)={'PASS' if isolated_ok else 'FAIL'}")
    print("=" * 78)
    return 0 if (real_ok and isolated_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
