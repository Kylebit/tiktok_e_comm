# 派单与回执格式

职责、授权、状态和 Git 规则统一见[线程治理](../THREAD_OPERATING_MODEL.md)。此页只定义可恢复的工作包记录；字段可以来自本轮明确消息和已审工单，不因缺少重复模板文字再索批。确实缺失权限或决定时仅暂停依赖动作并推进独立部分。

## Work Order

| 字段 | 内容 |
| --- | --- |
| work_order_id / revision | 稳定工单 ID 与本次范围修订 |
| owner | 当前执行 task_id / agent ID、逻辑职责；不用旧标题代替 |
| outcome / scope | 可验收结果、精确文件/函数/数据范围及排除项 |
| inputs | 已交上游精确提交/schema/工件、未交缺口与 producer |
| base | 已核验仓库、exact base commit、独立 worktree 和 branch |
| outputs | 交付路径与合同、报告或资产清单 |
| acceptance | 受影响验证集合、浏览器范围、失败条件与未覆盖项 |
| authority | 已授权本地动作；外部业务写、auth_writes、付费/上传分别记录范围与证据，无则 none |
| host_policy | 本次实际工具权限/no_escalation；不覆盖宿主策略 |
| git_policy | commit 要求、push/服务替换是否明确获准 |
| handoff | 回传接收者、下一负责人和可执行 next_action |
| current_state_source | 当前计划/协调状态/验收回执的路径、`as_of` 与 supersedes；不引用旧 active 图标代替 |
| repeat_work_check | 已完成且不得重复的结果、继续项、可归档候选；若返工，写明新证据或范围变化 |
| approval_reuse | 可复用的批准回执及其冻结对象/阶段/目标/动作/限制；只有这些字段变化才列新的决定缺口 |
| authority_receipt_ref / reusable_scope | 原批准证据引用；`actor`、`action_class`、精确对象/目标、候选/快照 digest、限制/预算和到期/撤销条件 |

UI 附加字段：实际页面/端口身份、桌面/窄屏、主操作与错误/unknown态、独立浏览器审查者、网络隔离。外部业务动作附加字段按[授权规则](../THREAD_OPERATING_MODEL.md#authority)记录精确平台/目标/动作、冻结批准、preflight、幂等/数量、恢复和正式回读。

<a id="resume"></a>
## ACK 与中断恢复

1. 中断、上下文压缩或重新接管后，先读当前有效工单/协调状态与最后实际回执；确认原包是否已交付、转交或变更。旧问题和附件不能替换当前目标。
2. 核验实际 root、base/HEAD、owner/task_id、允许写入、已完成证据、next_action，并列出已完成不重复/继续/待决定。先检查已有命令/测试进程结果，不重复启动仍运行的任务；保留先前有效产物。
3. 回复下面的短 ACK，随即执行同范围的第一条实际命令/编辑，并保存结果。这是内部接管检查，不增加用户批准。
4. 交付后回传工件与 next_owner/next_action；若后续包已派且授权清晰则接续。协调者核对 ACK、首动作与产物；纠偏送达、心跳或 turn completed 都不能替代这一闭环。

```text
work_order_id / revision:
owner / task_id:
git_top_level / branch:
base / HEAD / status-uall:
authority / host_policy:
last_verified_receipt / completed:
repeat_work_check / approval_reuse:
next_action / first_actual_command:
```

## 交付回执

```text
work_order_id / revision / owner:
outcome_and_exact_scope:
changed_files / contract_or_migration_impact:
source_branch / source_commit / parent_base:
validation_commands_and_environment / artifact_paths:
actual_results / skipped_or_not_run / known_failures:
business_writes / auth_writes / paid_calls / uploads:
protected_WIP_and_artifacts / clean_or_described_changes:
remaining_risks / next_owner / next_action:
```

组合回执另列 source→重放 commit 映射、最终 head、冲突取舍和受影响组合验证。状态含义见[状态表](../THREAD_OPERATING_MODEL.md#可观察状态)；局部交付与最终发布范围必须区分。
