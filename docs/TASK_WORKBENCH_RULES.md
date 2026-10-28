# 任务工作台：用户业务规则

本入口统一展示已接受的商品、补货、利润、工程、清理及其他工作。执行系统独立；任务模板不限制展示种类。达人、视频的观察来源或接管未确认时，明确显示交接状态待确认，不能显示为已接管或健康。

首页保留安排任务、最终审核、继续已有任务。默认显示当前真实工作、需要用户决定的事项和结果；新建配置、旧记录、身份代码、哈希与技术证据折叠。用户只处理真正业务决定、最终审核、本人登录、新增费用或超授权动作。Agent 负责日常观察、诊断、既有授权内安全恢复和验证跟进；不得把普通技术错误或可估参数转成用户待办。

每条工作记录具有稳定来源身份、目标、实际阶段、最近证据/观察时间、阻塞、负责人边界及结果入口。读页面成功不是心跳；文件存在不是正在运行。没有可靠心跳时显示证据时间和当前状态未知。过期、来源变化和读取失败保留旧记录并标明，不能清零成“没有任务”或虚构百分比。历史完成证据仍只是该范围交付记录，不扩成新商业结果。

涉及商品的任务描述、列表、详情和审核入口，必须同时给准确商品主图/缩略图及可识别名称。身份绑定包括身份类型、准确 ID、目标范围、名称来源和图片来源指纹。ID 保留在详情。不得按相似名称、位置、另一个 SKU 或废弃商品回填图片；图片身份不匹配或来源不可读时显示主图缺失/待核。换品、刷新和返回时必须更换同一身份的名称和图片，不能残留上一个商品缩略图。终审仍使用原冻结候选，工作台图片不能代替其逐目标证据。

工作汇总清单由协调者维护，生产者提供有时间与来源的观察记录。本观察接口不创建执行任务、不派单、不批准、不重试、不发布、不清除 UNKNOWN，也不代表可以向旧 Codex 会话追加消息。UNKNOWN 沿原 run/attempt/意图身份只读对账；任何新增权限、凭据、费用或超范围外部写入按原授权边界处理。

配置 `ORBIT_WORK_OBSERVATIONS_MANIFEST` 时必须明确采用清单与源权限。清单不得包含密码、令牌、联络资料、完整商家响应或私有配置。未绑定源或完整清单尚未确认时明确显示覆盖缺口，不能宣称所有任务已接入或持续监控已启动。

本候选不改正在维护的商品 Skill。独立 Skill 入口是 `skills/task-workbench-observation/SKILL.md`；软件、用户规则和该入口在同一候选中交付，正式安装/采用另做精确来源与单 writer 检查。

现有商品上架表单中的 `scope.offer_id` 是“采集箱 Offer ID”的旧 API 字段，展示时按 `common_collect_box_id` 绑定，不能推定为 Product Center 的规范身份。已有 Operations 任务与交接记录仅按准确 `operations_task_id` 合并；原冻结候选的审核入口继续由原任务契约计算，不能因合并交接记录而被隐藏。
## Adopted evidence contracts and maintenance

The observation manifest's own `complete` or `accepted_session_ids` declarations do
not establish coverage. `scope_ref` pins a separately reviewed coordinator scope
receipt (`orbit-work-scope/v1`, with `sessions[].session_id`) or the verified takeover
baseline (`continue_existing_tasks[].thread_id`). Missing, changed or uncovered scope
is shown as incomplete. Previously read omitted records remain visibly unverified.
Coverage of the seven delegated tasks does not mean creator/video ownership or their
heartbeat has been accepted.

Each verified product name/image requires `lineage`, a hashed local
`orbit-product-observation-lineage/v1` receipt. It pins typed identity, the normalized
display label, original title/facts and original download receipt. For a COMMON item,
the original state COMMON ID, 1688 supplier ID/URL and download COMMON/supplier IDs
must agree. An image must be an exact `ORIGINAL_BYTES_DOWNLOADED` path and SHA member
of that original receipt. Image serving rechecks the lineage and original references.
`orbit-product-source/v1` is the equivalent typed normalized source contract; it is
used by explicit synthetic tests. A display label is a coordinator normalization,
not an assertion of canonical Product Center identity or an approved business fact.

One designated coordinator owns the explicitly configured observation manifest.
`scripts/update_work_observations.py` validates a dated producer/coordinator update
envelope (`orbit-work-observation-update/v1`) and its separately hashed receipt.
Both bind original `work_id`, `source_session_id`, `observed_at` and all `changes`.
The command defaults to validation; `--apply` requires the current manifest SHA,
acquires an exclusive sibling lock, rechecks evidence/revision and replaces the
manifest atomically. A new date without a matching new receipt is rejected. Identity,
owner, handover and Operations execution mapping cannot be changed by this updater.
Scope/owner changes need a separately reviewed coordinator manifest revision.

Run with the adopted Python runtime and absolute paths:

```
python -I -B -X utf8 scripts/update_work_observations.py --manifest <absolute-manifest> --update <absolute-envelope> --expected-sha256 <current-sha>
```

Add `--apply` only when updating the authorized coordinator manifest. Store the new
receipt durably before applying. Refresh then reads the new state without renewing
evidence times, writing a lease, admitting work or replaying a queue. This command
does not install a monitor, scheduler, session messaging or a domain writer. Without
that separate supported producer connection, real-time status remains unknown.
