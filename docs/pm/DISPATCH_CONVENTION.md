# 派发任务约定（Dispatch Convention）

本约定适用于 CEO 向固定 `00`–`05` 线程派发的正式工作，以及固定线程
对临时 Agent 的有界辅助派发。Codex 任务消息和 Git 证据是事实来源；
飞书、EigenFlux、邮件等只可作为可选通知镜像，不得成为执行前置依赖。

## 分派原则

1. CEO 默认负责拆分、审查、集成和发布，不默认包办业务实现。
2. 单域工作派给对应固定线程；涉及两个以上业务域时，必须给所有相关
   固定线程分别建立 Work Order，并明确合同交接和集成顺序。
3. 固定 `00`–`05` 优先于临时 Agent。临时 Agent 不取得领域所有权，
   不替代固定线程的 ACK、commit、测试和回传。
4. 正式 UI 由所属域实现，`00` 负责独立真实浏览器验收。验收发现问题
   回到所属域修复，不能形成第二条业务实现路径。
5. 外部平台写入只能派给 `03 渠道运营` 单线执行，并由 CEO 监督。
   未明确授权时均为只读或 dry-run。

CEO 可直接编码的有限例外、线程职责和状态语义见
[`../THREAD_OPERATING_MODEL.md`](../THREAD_OPERATING_MODEL.md)。

## Work Order 最小字段

每条正式派发必须包含：

| 字段 | 要求 |
| --- | --- |
| `work_order_id` | 唯一、稳定，例如 `WO-20260727-GOV-001` |
| `owner` | 固定线程编号、领域名和目标 task/thread ID |
| `outcome` | 可验收的业务或治理结果，而非操作步骤堆叠 |
| `scope` | 允许修改的文件、模块、数据与明确禁止范围 |
| `inputs` | 上游合同、fixture、文档或已有提交 |
| `base` | 正式仓库、基线 commit、独立 worktree、branch |
| `outputs` | 文件、合同、报告或可运行产物 |
| `acceptance` | 必跑测试、浏览器场景、失败门槛和人工验收点 |
| `external_write` | `none`、`dry-run` 或经批准的精确目标与动作 |
| `host_approval_policy` | 固定线程本地工程/测试/只读验收默认且应写明 `never` |
| `no_escalation` | 默认 `true`；命令触发宿主审批时放弃并采用非升级等价路径 |
| `git_policy` | commit 要求；push 默认 `forbidden`，除非明确授权 |
| `handoff` | 回传对象、格式、风险和下一责任人 |

缺少 `owner`、`base`、`scope`、`acceptance`、`external_write`、
`host_approval_policy` 或 `no_escalation` 时，
执行线程只能 ACK 后澄清或保持只读，不能自行扩大权限。

`host_approval_policy`/`no_escalation` 管理宿主工具调用，不替代
`external_write` 所表达的业务授权。即使 Kyle 已批准业务动作，固定线程
也不得用该批准请求普通 shell、pytest、worktree、Git 或只读验收的宿主
escalation。预计触发审批的命令必须改写为非升级、非破坏的等价路径。
只有确实需要 Kyle 完成的外部业务授权，或无安全替代方案的高风险必要
动作，才作为明确决策上报；不得把工具审批提示转交 Kyle 代为处理。

## ACK 与状态

执行线程使用同一个 Work Order ID 回复 ACK，并报告：

```text
ACK
git_top_level: ...
branch: ...
head: ...
status: clean | existing-changes-described
authority: read-only | code-write | external-write-exact-scope
host_approval_policy: never
no_escalation: true
```

状态只由消息和证据推进：

`DRAFT → DISPATCHED → ACKED → RUNNING → DELIVERED → REVIEWED → INTEGRATED → ACCEPTED`

`BLOCKED` 和 `CANCELLED` 可从任一未完成状态进入。`BLOCKED` 必须对应
无法通过安全替代路径消除的业务决定或依赖；宿主命令显示
`waitingOnApproval` 不构成业务 `BLOCKED`。执行线程应取消或放弃该命令，
改用非升级路径并继续其余可完成步骤。任务标题、
`active/idle/notLoaded`、分支名或静态看板文案都不能自动推进状态。

## 单 writer 与 Git

- 每个代码 Work Order 使用独立 worktree 和独立 branch。
- 开始编辑前必须核验 Git top-level、branch、HEAD 和 status。
- 一个 worktree/branch 同时只有一个 writer；并行任务不得编辑重叠文件。
- 已有修改属于原作者。不得通过 reset、clean、checkout 覆盖或强行吸收。
- 测试使用当前独立 worktree 内的 Work Order 专用 `basetemp`。临时测试
  目录无需为提交删除；可以忽略或保留，并通过精确文件列表暂存交付。
  禁止为了“干净”递归删除临时目录，禁止为清理请求宿主 escalation。
- 长期代码、文档、修复和重构交付必须形成聚焦 commit，并回传 hash。
- 只读审查、无文件变化的探针不制造空 commit。
- `git push` 默认禁止；只有 Kyle 或正式发布流程在 Work Order 中明确
  授权后才允许。

## UI 与外部写入附加字段

正式 UI Work Order 还必须写明：

- 页面/路由及所属域；
- 桌面和窄屏 viewport；
- 主操作、异步反馈、错误态与 `unknown/unavailable` 语义；
- `00` 独立浏览器验收人；
- 浏览器 console/page error、computed visibility、overflow 和外网阻断。

外部平台写入 Work Order 还必须写明：

- 由 `03` 执行的唯一 writer；
- 平台、店铺/租户、对象和精确动作；
- Kyle 的明确批准证据；
- dry-run/preflight、幂等键、预期变更数；
- 回滚或恢复办法及审计产物。

上述任一字段缺失时不得写入。CEO、`00`/`01`/`02`/`04`/`05` 和临时
Agent 均不得代替 `03` 执行。

## 交付回执

固定线程的 `DELIVERED` 回执至少包含：

```text
work_order_id:
owner:
outcome:
changed_files:
contract_or_migration_impact:
tests_and_browser_evidence:
source_branch:
source_commit:
external_writes: 0 | exact audited writes
remaining_risks:
next_owner: CEO
```

CEO 的 Integration Receipt 记录源提交到主分支提交的映射、集成方式、
完整回归、外部写入证据、例外及到期日。`DELIVERED` 不等于
`INTEGRATED`，`INTEGRATED` 也不等于 Kyle 已 `ACCEPTED`。

进展可镜像到飞书等系统，但镜像失败不得阻止本地安全开发、回传或验收，
也不得覆盖 Codex Work Order 与 Git 证据。
