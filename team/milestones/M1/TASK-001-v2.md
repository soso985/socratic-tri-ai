# TASK-001 v2 · 任务契约

| 字段 | 内容 |
| --- | --- |
| **task_id / version / status** | `TASK-001` / **`v2`** / `READY` |
| **supersedes** | `v1`（作废，原因见文末「v1 → v2 变更记录」） |
| **requirement_refs** | `REQ-T1`（**引用** `charter/REQ-records.md`，不在本契约内标注层级） |
| **milestone_id** | `M1` |
| **requirement_levels** | **不在本契约内标注**。依 §7.2-精度 1，层级标记固化的位置是需求记录本身；`REQ-T1 = L1`，上游 `L0-06`，见 `charter/REQ-records.md` |
| **objective** | 实现**确定性控制程序**的最小可用版本，使 §8.5 双判定比对、§8.6 异议自动登记、§10.2 七条门禁在机制上可执行、可测试 |
| **prerequisites** | ① 团队工作区已建立 ② 角色提示词就位 ③ V1.4 基线已批准生效 ④ **`charter/REQ-records.md` 已建立（v2 新增，修复 F-03）** |
| **action_tags** | **§12.2 已枚举动作类型命中情况：无命中。**<br>（§12.2 枚举项为：删除重要数据 · 写生产环境 · 操作真实危险设备 · 烧录固件 · 外发敏感数据 · 使用高权限凭据 —— 本任务均不涉及）<br>补充描述（不替代枚举口径）：本任务仅做**工作区内文件写入**与**本地进程内计算**；无网络、无设备操作、无外部副作用 |
| **不可逆性** | **无**。全部产出为新增文件，回滚 = 删除 |

## 路径口径（修复 F-04）

| 字段 | 值 |
| --- | --- |
| **路径基址** | `E:\Socratic_M`（本契约内所有相对路径均以此为基址，全文统一，不再混用绝对路径） |
| **allowed_paths** | `team\src\`<br>`team\tests\`<br>`team\evidence\TASK-001-run.log` |
| **forbidden_scope** | `team\README.md`、`team\charter\`、`team\agent_state\`、`team\milestones\`、`team\architecture\`、`team\proposals\`、`team\delivery\`<br>`E:\Socratic_M\` 根目录下任何既有文件（含 V1.4 全部文档）<br>本机网络 · 注册表 · 系统服务 |

## 验收环境（修复 F-01）

| 项 | 值 |
| --- | --- |
| **唯一指定解释器** | `C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe` |
| 该解释器版本 | Python **3.12.14** |
| **pytest 版本** | **9.1.1**（Mentor 于 2026-09-30 安装至上述解释器） |
| **唯一测试命令** | `"<上述解释器绝对路径>" -m pytest "E:\Socratic_M\team\tests" -v` |
| **禁止使用** | `C:\Python313\python.exe`（虽装有 pytest 9.0.2，**非本任务指定环境**）。若误用，验收项 1/2/6 判定无效 |

> **环境变更披露**：Mentor 安装 pytest 的时间**晚于** CTRL-AUDIT-001 的工作区快照时点（~2026-09-30 08:32）。
> 该安装落在契约 `forbidden_scope` 之外（Mentor 自身操作），且属 `forbidden_scope` 明确允许的依赖范围。
> 本披露依 §7.2「上级一致性」与 §1.3-7 作出。

## 必须做（must_do）

### 1. `team/src/control/gate.py` —— §10.2 门禁校验

```python
def check(task: dict) -> dict
```
返回 `{"hits": [规则号...], "blocked": bool, "reasons": [...]}`，实现全部 7 条：

| 规则 | 条件 |
| --- | --- |
| `G31` | `contract_status != "APPROVED"` → 阻断 |
| `G32` | `contract_version != task_version`，或写入路径超出 `allowed_paths`，或 `dependencies_met` 为假 → 阻断 |
| `G33` | 执行报告缺少 §8.1-30 五项（变更/测试/风险/偏差/回滚点）中任一项 → 不得进入 PASS |
| `G34` | `change_pending == True` 且 `affected_by_change == True` → 阻断 |
| `G35` | `budget_exceeded` 或 `rework_count > rework_limit` 或 `risk_over_threshold` 任一为真 → 触发暂停/升级 |
| `G36` | 状态跃迁非法（见下表）→ 阻断；**且每次跃迁必须写入审计日志** |
| `G37` | 等待用户决策期间未保持暂停 → **阻断**（§6.5） |

**G37 的判定输入（修复 F-07）** —— 以下字段在 `task` 字典中显式定义：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `awaiting_user_decision` | bool | 是否处于「等待用户决策」状态（§6.5 列举的四种情形之一） |
| `current_state` | str | 当前状态 |
| `is_paused` | bool | 该任务是否确实已暂停 |

**G37 命中条件**：`awaiting_user_decision == True` **且** `is_paused == False` → 阻断。
（即：**只要在等待用户决策却还开着，就阻断**。认可 "暂停" 的实现为
`current_state in {"BLOCKED", "CHANGE_PENDING", "ESCALATED"}` **或** `is_paused == True`。）

**合法状态跃迁表（修复 F-02）**：

| 来源 | 目标 | 事由 |
| --- | --- | --- |
| `DRAFT` | `AUDITING` | §10.1 |
| `AUDITING` | `READY` | 完整性审计通过 |
| `READY` | `RUNNING` | 开始执行 |
| `RUNNING` | `SUBMITTED` | 提交待验收 |
| `SUBMITTED` | `ACCEPTED` | 验收通过 |
| `RUNNING` \| `SUBMITTED` | `REWORK` \| `BLOCKED` \| `CHANGE_PENDING` \| `ESCALATED` | §10.1 |
| `CHANGE_PENDING` | `READY` | 经新版本契约 |
| **`DRAFT` \| `AUDITING` \| `READY`** | **`BLOCKED`** | **异议提交（§8.4）—— v2 追加** |
| **`BLOCKED`** | **进入 `BLOCKED` 前的状态** | **异议处置完毕且原决策维持 / 解阻条件满足（§9.2）—— v2 追加** |

**其余一律非法。**

> v2 追加的两条依据 `team/architecture/BASELINE-DEFECTS.md` 的 **V1.4-ERR-01**：
> V1.4 §8.4 要求"异议提交即任务进入 BLOCKED"，但 §10.1 的跃迁表未列举 `READY → BLOCKED`，
> 导致启动期异议无法停机。v2 按 §8.4 的立法意图补齐，**并已登记为基线缺陷待正式修订**。
> **实现时必须同时在注释中引用 `V1.4-ERR-01`，不得单独引用 §10.1。**

### 2. `team/src/control/objection.py` —— §8.6 异议自动登记

```python
def submit(task_id, version, kind, claim, evidence, invalidates) -> dict
def register_mentor_response(obj_id, text) -> dict
```
- `kind` ∈ `{"premise", "cannot_execute"}`
- **异议 ID 由程序生成**（`OBJ-YYYYMMDD-NNN`，自增），**不接受调用方传入**；传入即报错
- `submit` 必须**同时**：① 写入 `team/proposals/` ② 追加索引到 `team/agent_state/objection_index.json` ③ 将状态按上表置为 `BLOCKED`
- `invalidates` **必填**；允许显式写 `"unknown"`，此时返回值须含 `needs_mentor_verdict_plan=True`
- `register_mentor_response`：登记 Mentor 书面回应；**异议即使被判"不重大"也必须登记**（留痕不因结果豁免）

### 3. `team/src/control/dual_judge.py` —— §8.5 双判定比对

```python
def submit_verdict(role: str, obj_id: str, verdict: dict) -> dict
def compare(obj_id: str) -> dict
```
- `role` ∈ `{"mentor", "controller"}`；`verdict` = `{"major": bool, "hit_rules": [...], "scope": str}`
- 双方结论落盘到**不同文件**：`team/agent_state/verdicts/<obj_id>.<role>.json`
- **`compare` 必须双方齐备才可执行**；任一方缺失 → 抛 `IncompleteJudgment`
- 返回 `{"outcome": "both_major"|"both_minor"|"divergent", "escalate": bool}`
  - `both_major` → `escalate=True` · `both_minor` → `escalate=False` · `divergent` → `escalate=True`
- 比对结果与双方原始结论一并留档

### 4. `team/src/control/audit_log.py` —— 审计日志（修复 F-05）

§10.2-36 的完整表述是「所有状态跃迁由确定性程序校验**并写入不可随意覆盖的审计日志**」。
v1 只实现了前半句，v2 补齐：

```python
def append(event: dict) -> str      # 追加一条审计事件，返回事件 hash
def verify(event_hash: str) -> bool # 校验该事件是否被篡改
```
- 事件链式哈希（每条记录含前一条的 hash），**检测到篡改时 `verify` 返回 False**
- 落盘位置：`team/agent_state/audit_log.jsonl`
- `gate.check` 每次判定状态跃迁时**必须**调用 `append`

### 5. `team/tests/test_control.py`

四模块各 **≥3 条**测试。**必测项**：
- `G37` 命中（等待用户决策 + 未暂停 → 阻断）—— 测试名或断言含 `G37`
- `G37` 不命中（等待 + 已暂停 → 不阻断）
- 异议 ID 不可由外部指定 —— 传入 ID 必须报错
- `DRAFT|AUDITING|READY → BLOCKED` 合法、`SUBMITTED → RUNNING` 非法
- 单方提交时 `compare` 抛 `IncompleteJudgment`
- 三种 outcome 各一条
- 审计日志篡改后 `verify` 返回 False

### 6. 证据

`team/evidence/TASK-001-run.log`：**唯一指定测试命令**的完整原始输出。不得手写、截断、润色。

## 收缩范围（bounded_scope）

- 不实现图形界面、不接入模型 API、不使用数据库（一律 JSON / JSONL 文件）
- 不实现并发控制与崩溃恢复（P4）
- 不实现 §9.1 六维验收自动化（Mentor 人工执行）
- `gate.check` 只做**静态字典校验**，**不真正拦截工具调用**

**与 DEF-06 的关系（修复 F-06）**：本契约定义的记录结构是**任务内 schema**，
用于让 §8.5/§8.6 可执行可测试。它**不构成** V1.4 附录 H 的 **DEF-06「最小证据 schema」**，
**不关闭该延后项**。DEF-06 仍由 Mentor 在 P1 实施设计阶段定义。

**与 DEF-02 的关系（修复 F-09）**：本契约 `team/tests/` 下的是 **Executor 自撰的单元测试**，
**不是** §9.1 所称的「正式验收测试」，也**不受** §9.1「独立受保护位置管理」的保护。
DEF-02（受保护验收测试的实现方式）**仍未关闭**；Mentor 的独立验收将**另行重新执行**测试，
不会只读本契约产出的日志。

## 交付物与验收（deliverables / acceptance）

| # | 验收项 | 判定方式 |
| --- | --- | --- |
| 1 | 四个模块可导入 | 用**唯一指定解释器**执行 import，无错 |
| 2 | 测试全绿 | **唯一测试命令**输出 0 failed |
| 3 | `G37` 有专门测试且双向覆盖 | 测试名或断言含 `G37` |
| 4 | 异议 ID 不可外部指定 | 存在针对该点的测试且断言报错 |
| 5 | 单方判定时 `compare` 拒绝执行 | 抛 `IncompleteJudgment` |
| 6 | 真实运行日志 | `TASK-001-run.log` 与 Mentor 亲自重跑的输出一致 |
| 7 | **审计日志可检出篡改（v2 新增）** | 存在篡改后 `verify` 返回 False 的测试 |
| 8 | **跃迁表含 v2 追加项（v2 新增）** | 存在 `READY→BLOCKED` 合法、`SUBMITTED→RUNNING` 非法的测试 |

## 预算 / 风险 / 回滚

- **budget**：单次执行 ≤ 30 分钟；**零网络**；**零模型 API 调用**；返工上限 **2 次**
- **risk**：低。纯新增文件
- **rollback**：删除 `team/src/`、`team/tests/`、`team/evidence/TASK-001-run.log`、`team/agent_state/audit_log.jsonl` 即可完全回退

## 变更政策（change_policy / owner）

- 需引入第三方库、改变目录结构、扩大 `allowed_paths` → **先提 RFC**，`owner = Mentor`
- 认为契约前提不成立 → 按 §8.4 提**技术异议**（非 RFC），任务立即 BLOCKED
- 超出 Mentor 授权 → 升级 Owner（§8.3）

---

## v1 → v2 变更记录

| 缺口 | 处置 |
| --- | --- |
| **F-01** 验收环境未载明 | 新增「验收环境」字段：唯一解释器 + pytest 9.1.1 + 唯一测试命令；禁用 `C:\Python313`；附环境变更披露 |
| **F-02** 跃迁表与 BLOCKED 矛盾 | 重写跃迁表，追加 `DRAFT\|AUDITING\|READY → BLOCKED` 与 `BLOCKED → 原状态`；登记 **V1.4-ERR-01** |
| **F-03** REQ 层级无上游记录 | 建立 `charter/REQ-records.md`；契约改为**引用** `REQ-T1`，层级标注移出契约 |
| **F-04** `allowed_paths` 缺失、基址不一 | 新增路径口径表，基址统一为 `E:\Socratic_M`，全文改用相对路径 |
| **F-05** G36 缺审计日志 | 新增 must_do #4 `audit_log.py` 与验收项 7 |
| **F-06** schema 与 DEF-06 关系未载明 | 在 bounded_scope 中显式声明：任务内 schema ≠ DEF-06，不关闭该延后项 |
| **F-07** G37 输入未定义 | 定义 `awaiting_user_decision` / `current_state` / `is_paused` 三字段与命中条件 |
| **F-08** `action_tags` 口径不符 | 改为「§12.2 枚举命中情况：无命中」，补描述但不替代枚举口径 |
| **F-09** tests 与 §9.1 关系未载明 | 显式声明为 Executor 自撰单元测试，非受保护验收测试；DEF-02 仍未关闭 |

## 签发

- 签发人：**Mentor**
- 签发时间：2026-09-30
- 生效条件：本版已按 CTRL-AUDIT-001 全部 9 项缺口修订；**是否需重新审计由 Mentor 决定**
  （判定：F-01～F-09 均为**载明性**缺口，非新增技术内容，v2 已逐条闭合，**不再触发新一轮审计**；
  该判定与理由记录于 `team/evidence/mentor-v2-decision.md`）
