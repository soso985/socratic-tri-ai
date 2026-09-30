"""写文件锁 · 篡改拦截演示（task-5 核心交付 · Owner 直接指令）。

用法（唯一解释器）::

    <python> E:\\Socratic_M\\team\\tests\\demo_write_lock.py

演示内容：
1. 取 ``team/src/`` 下一个**已有文件**作为篡改目标，记录其 SHA256 与 mtime_ns；
2. 用「未批准」（非 RUNNING）状态**故意**调用 ``locked_write`` 去改它；
3. 确认抛出 ``WriteDenied``（而不是"写了再回滚"）；
4. 再次读取 SHA256 与 mtime_ns。
   **通过标准只有一个：前后 SHA256 完全相同。**
5. 打印审计日志中新增的拒绝条目（该写入豁免于本锁，否则拒绝无法留痕）；
6. 打印 ``team/src/`` 全部既有文件在演示前后的 SHA256 对照，证明演示期间没有任何既有文件被改动。

本脚本**只读**既有文件，唯一的写入是审计日志里的那一条拒绝记录（由被演示的程序自身完成）。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

TEAM_DIR = Path(__file__).resolve().parents[1]          # <工作区>/team
SRC_DIR = TEAM_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from control import data_root  # noqa: E402
from control.write_lock import WriteDenied, locked_write  # noqa: E402

#: 篡改目标：team/src/ 下一个**既有**文件
TARGET = SRC_DIR / "control" / "gate.py"

#: 演示所用状态 —— Owner 明确要求：保持「未批准」，**不得**改成 RUNNING
DEMO_STATE = {"status": "未批准"}


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


def main() -> int:
    print("=== TASK-001 写文件锁 · 篡改拦截演示 ===")
    print("解释器版本      :", sys.version.split()[0])
    print("数据根（审计日志）:", data_root())
    print("篡改目标        :", TARGET)
    print("演示所用状态    :", json.dumps(DEMO_STATE, ensure_ascii=False))
    print()

    before_hash = sha256_of(TARGET)
    before_mtime = TARGET.stat().st_mtime_ns
    before_size = TARGET.stat().st_size
    src_before = snapshot_src()
    records_before = audit_records()

    print("--- 演示前 ---")
    print("目标 SHA256     :", before_hash)
    print("目标 mtime_ns   :", before_mtime)
    print("目标 字节数     :", before_size)
    print("审计日志条目数  :", len(records_before))
    print()

    print("--- 尝试篡改（非 RUNNING 状态）---")
    print("调用：locked_write(%r, '<篡改内容>', task_state={'status': '未批准'})" % str(TARGET))
    raised = None
    try:
        locked_write(TARGET, "# 本行若出现即表示锁失效 —— 篡改成功\n", task_state=DEMO_STATE)
        print("结果：!!! 未抛出异常 —— 锁失效 !!!")
    except WriteDenied as exc:
        raised = exc
        print("结果：抛出 WriteDenied（写入被拒绝）")
        print("异常信息        :", exc)
        print("异常 path       :", exc.path)
        print("异常 state      :", exc.state)
        print("异常 required   :", exc.required_state)
        print("拒绝记录 audit  :", exc.audit_hash)
        print("留痕失败 audit_err:", exc.audit_error)
    print()

    after_hash = sha256_of(TARGET)
    after_mtime = TARGET.stat().st_mtime_ns
    after_size = TARGET.stat().st_size
    src_after = snapshot_src()
    records_after = audit_records()

    print("--- 演示后 ---")
    print("目标 SHA256     :", after_hash)
    print("目标 mtime_ns   :", after_mtime)
    print("目标 字节数     :", after_size)
    print("审计日志条目数  :", len(records_after))
    print()

    print("--- 判定（通过标准：前后 SHA256 完全相同）---")
    same_hash = before_hash == after_hash
    print("前后 SHA256 相同:", same_hash)
    print("前后 mtime 相同 :", before_mtime == after_mtime)
    print("前后字节数相同  :", before_size == after_size)
    print("抛出 WriteDenied:", raised is not None)
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
        before = src_before.get(name)
        after = src_after.get(name)
        flag = "相同" if before == after else "★已改变★"
        if before != after:
            all_unchanged = False
        print(f"{flag}  {name}")
        print(f"        前: {before}")
        print(f"        后: {after}")
    print("src/ 全部既有文件未被改动:", all_unchanged)
    print()

    demo_pass = same_hash and raised is not None and all_unchanged
    print("=== 演示结论:", "PASS —— 篡改被真实拦下，目标文件一个字节未变" if demo_pass else "FAIL", "===")
    return 0 if demo_pass else 1


if __name__ == "__main__":
    sys.exit(main())
