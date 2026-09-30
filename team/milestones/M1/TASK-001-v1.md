# TASK-001 v1 · 任务契约

| 字段 | 内容 |
| --- | --- |
| **task_id / version / status** | `TASK-001` / `v1` / `READY`（经 Controller 完整性审计后生效） |
| **requirement_refs / milestone_id** | `REQ-T1`；里程碑 `M1` |
| **requirement_levels** | `REQ-T1` = **L1**（派生自 V1.4 §8.5 / §8.6 / §10.2）<br>关联 **L0**：V1.4 §1.3-6「高风险工具操作依赖**程序化权限隔离，而非仅依赖提示词**」 |
| **objective** | 实现**确定性控制程序**的最小可用版本，使 §8.5 双判定比对、§8.6 异议自动登记、§10.2 七条门禁在机制上可执行 |
| **prerequisites** | ① 团队工作区已建立（`team/`）② 三个角色提示词已就位 ③ V1.4 基线已批准生效 |
| **action_tags** | 文件写入（`team/src/`、`team/tests/`、`team/evidence/`）；**无网络**；**无设备操作**；**无外部副作用**；**不可逆性：无** |

## 必须做（must_do）

### 1. `src/control/gate.py` —— §10.2 门禁校验

```python
def check(task: dict) -> dict
```
返回 `{"hits": [规则号...], "blocked": bool, "reasons": [...]}`，须实现全部 7 条：

| 规则 | 条件 |
| --- | --- |
| `G31` | 无已批准契约（`contract_status != "APPROVED"`）→ 阻断 |
| `G32` | `contract_version != task_version`，或写入路径超出 `allowed_paths`，或依赖未满足 → 阻断 |
| `G33` | 执行报告缺少必要证据项（§8.1-30 五项：变更/测试/风险/偏差/回滚点）→ 不得进入 PASS |
| `G34` | 存在待批变更（`change_pending=True`）且该任务受影响 → 阻断 |
| `G35` | 达到预算、返工次数或风险阈值 → 触发暂停/升级 |
| `G36` | 状态跃迁非法（见下）→ 阻断 |
| `G37` | `awaiting_user_decision=True` 且任务未处于暂停 → **阻断**（§6.5） |

合法状态跃迁（§10.1）：`DRAFT→AUDITING→READY→RUNNING→SUBMITTED→ACCEPTED`；
`RUNNING|SUBMITTED → REWORK|BLOCKED|CHANGE_PENDING|ESCALATED`；`CHANGE_PENDING → READY`（经新版本）。
**其余一律非法。**

### 2. `src/control/objection.py` —— §8.6 异议自动登记

```python
def submit(task_id, version, kind, claim, evidence, invalidates) -> dict
def register_mentor_response(obj_id, text) -> dict
```
- `kind` ∈ `{"premise", "cannot_execute"}`（分别对应「前提有问题」「无法执行」）
- **异议 ID 由程序生成**（格式 `OBJ-YYYYMMDD-NNN`，自增），**不接受调用方传入**
- `submit` 必须**同时**：① 写入 `proposals/` ② 追加索引到 `agent_state/objection_index.json` ③ 将 `task.status` 置为 `BLOCKED`
- `invalidates`：**必填**，须指认「若该异议成立，会导致哪一条已验收记录或里程碑结论失效」；
  允许显式写 `"unknown"`，但须在返回值中标记 `needs_mentor_verdict_plan=True`
- `register_mentor_response`：登记 Mentor 的书面回应；**若异议已被判"不重大"，回应仍必须登记**（留痕不因结果而豁免）

### 3. `src/control/dual_judge.py` —— §8.5 双判定比对

```python
def submit_verdict(role: str, obj_id: str, verdict: dict) -> dict
def compare(obj_id: str) -> dict
```
- `role` ∈ `{"mentor", "controller"}`；`verdict` = `{"major": bool, "hit_rules": [...], "scope": str}`
- 双方结论落盘到**不同文件**（`agent_state/verdicts/<obj_id>.<role>.json`）
- **`compare` 必须在双方都提交后才能执行**；任一方缺失 → 抛 `IncompleteJudgment`
- 返回 `{"outcome": "both_major"|"both_minor"|"divergent", "escalate": bool}`
  - `both_major` → `escalate=True`（升级 Owner）
  - `both_minor` → `escalate=False`（按原决策继续，异议封闭但留痕）
  - `divergent` → `escalate=True`（交 Owner 裁决）
- 比对结果与双方原始结论一并留档

### 4. `tests/test_control.py`

三项功能各至少 **3 条**测试，须覆盖：
- 门禁：合法通过 / 每条规则至少一次命中 / **G37 是本任务的必测项**
- 异议：ID 自增且不可外部指定 / 提交后 task 置 BLOCKED / `invalidates` 为 `unknown` 时的标记
- 双判定：单方提交时 `compare` 抛异常 / 三种 outcome 各一条

### 5. 证据

`evidence/TASK-001-run.log`：真实运行 `pytest` 的完整输出（不得手写、不得截断）。

## 收缩范围（bounded_scope）

- **不**实现图形界面、**不**接入任何模型 API、**不**使用数据库（一律 JSON 文件）
- **不**实现持久化恢复与并发控制（属 P4）
- **不**实现 §9.1 六维验收的自动化（由 Mentor 人工执行）
- 允许简化：`gate.check` 只做**静态字典校验**，不负责真正拦截工具调用

## 禁止范围（forbidden_scope）

- 修改 `team/README.md`、`team/charter/`、`team/agent_state/roles/`、`team/milestones/`、`team/architecture/`
- 修改 V1.4 基线文档（`三 AI 自主项目开发系统-V1.4.md` 及同目录任何 md/pdf/docx）
- 改动 `E:\Socratic_M` 根目录已有文件
- 引入任何第三方依赖（仅允许 Python 标准库 + `pytest`）
- 触碰本机网络、注册表、系统服务

## 交付物与验收（deliverables / acceptance）

| # | 验收项 | 判定方式 |
| --- | --- | --- |
| 1 | 三个模块可导入 | `python -c "import ..."` 无错 |
| 2 | 测试全绿 | `pytest` 全部通过，0 failed |
| 3 | G37 有专门测试 | 测试名或断言中含 G37 |
| 4 | 异议 ID 不可外部指定 | 存在针对该点的测试 |
| 5 | 单方判定时 `compare` 拒绝执行 | 抛 `IncompleteJudgment` |
| 6 | 真实运行日志 | `evidence/TASK-001-run.log` 内容与实跑一致 |

## 预算 / 风险 / 回滚（budget / risk / rollback）

- **budget**：单次执行 ≤ 30 分钟；**零网络调用**；**零模型 API 调用**；返工上限 **2 次**
- **risk**：低。纯新增文件，不影响任何既有产物
- **rollback**：删除 `team/src/control/`、`team/tests/`、`team/evidence/TASK-001-run.log` 即可完全回退

## 变更政策（change_policy / owner）

- 需引入第三方库、改变目录结构、或扩大 `allowed_paths` → **先提 RFC**，`owner = Mentor`
- 认为契约前提不成立 → 按 §8.4 提**技术异议**（不是 RFC），任务立即 BLOCKED
- 超出 Mentor 授权 → 升级 Owner（§8.3）

## 签发

- 签发人：**Mentor**
- 签发时间：2026-09-30
- 生效条件：经 Controller 完整性审计（§5.2）且无阻塞性缺口
