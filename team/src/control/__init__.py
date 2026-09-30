"""确定性控制程序 —— TASK-001 v2 交付物（§8.5 / §8.6 / §10.2）。

契约：``team/milestones/M1/TASK-001-v2.md``（v2 为唯一有效版本）
需求：``REQ-T1``（``team/charter/REQ-records.md``），上游 L0-06

数据根（DATA ROOT）约定
-----------------------
契约把程序的运行期产物落在工作区内：::

    proposals/<OBJ-ID>.md              异议全文与 Mentor 书面回应（§8.6）
    agent_state/objection_index.json   异议索引（§8.6 / §11.1）
    agent_state/task_state.json        任务状态（§10.1）
    agent_state/verdicts/<...>.json    §8.5 双判定结论与比对留档
    agent_state/audit_log.jsonl        §10.2-36 不可随意覆盖的审计日志

默认数据根 = 本包所在工作区的 ``team/`` 目录（即 ``E:\\Socratic_M\\team``）。

**为什么数据根可注入**：契约未规定数据根可注入。但契约同时把
``team/agent_state/`` 与 ``team/proposals/`` 列入 Executor 的 ``forbidden_scope``
——即 Executor **编写**这些文件属越界，而程序**运行期**写入它们是契约明文要求。
为同时满足两者，本包支持用环境变量 ``CONTROL_DATA_ROOT`` 把数据根重定向到别处；
交付过程中运行测试即用此方式，使 Executor 自身不越界。
环境变量未设置时，行为与契约默认落点完全一致。

本模块不实现并发控制、崩溃恢复与幂等（``bounded_scope``：属 P4）。
"""

from __future__ import annotations

import os
from pathlib import Path

#: 重定向数据根的环境变量名
ENV_DATA_ROOT = "CONTROL_DATA_ROOT"

#: 工作区基址（契约「路径口径」：路径基址 = ``E:\Socratic_M``）
#: 本文件位于 ``<工作区>/team/src/control/__init__.py``，故 ``parents[3]`` 才是工作区根。
WORKSPACE_BASE = Path(__file__).resolve().parents[3]

#: 默认数据根 = ``<工作区>/team``
DEFAULT_DATA_ROOT = WORKSPACE_BASE / "team"


def data_root() -> Path:
    """返回当前数据根（环境变量优先，否则契约默认落点）。"""
    raw = os.environ.get(ENV_DATA_ROOT)
    if raw:
        return Path(raw)
    return DEFAULT_DATA_ROOT


def set_data_root(path) -> None:
    """重定向数据根（供测试与隔离运行使用）。"""
    os.environ[ENV_DATA_ROOT] = str(path)


def reset_data_root() -> None:
    """恢复默认数据根。"""
    os.environ.pop(ENV_DATA_ROOT, None)


__all__ = [
    "ENV_DATA_ROOT",
    "WORKSPACE_BASE",
    "DEFAULT_DATA_ROOT",
    "data_root",
    "set_data_root",
    "reset_data_root",
]
