# OrbitHive 知识怎么用

机器数据库和正式回执负责“发生了什么、截至何时”；Obsidian 记录“为何这样做、适用条件、下次如何验证”；聊天保存决定出处、当前负责人和下一步。先用一篇笔记完成一个真实问题的回流，不为整理知识重建数据库或批量改旧笔记。

Obsidian 的 Vault 是本地文件夹，可以打开既有文件夹；笔记可用 `[[目录/笔记]]` 或 Markdown 链接互相引用。接手时核对本次明确选择的根，不按历史 C/D 盘或“最近打开”猜测。上述产品用法见 [Vault 官方说明](https://help.obsidian.md/Getting+started/Create+a+vault)与[内部链接说明](https://help.obsidian.md/Linking+notes+and+files/Internal+links)（核对日期 2026-09-06）。本指南未连接或修改真实 Vault。

## 三种场景，各写一页

| 场景 | 先读什么 | 笔记写什么 | 何时更新或标过期 | 交给 agent 的具体任务 |
| --- | --- | --- | --- | --- |
| 商品事故复盘 | 精确 Offer/SKU/平台/目标、冻结候选与批准、run/report、正式回读、对应修复提交和回归 | 触发条件 → 观察 → 已证实根因/未证实假设 → 修复 → 适用范围；链接原回执，不复制秘密和全部响应 | 新回读推翻结论、代码或 provider 合同变化时新增修订并标旧结论被替代；只有假设先标待验证 | 读取指定回执与代码，核验根因，给出当前合同的最小修复或经验提案；笔记本身不授予平台重试权 |
| 补货决策 | 按国家/仓库/SKU 的有时点库存、有效订单需求、在途、到货时间和物流成本来源 | 为什么选某补货量；需求窗口、到货假设、排除项、决策人/出处、预计复核日期；把估算与已核验事实分开 | 新库存/在途/需求或交期变化就标旧方案需重算；到期未刷新只显示“截至某时点”，不继续标 CURRENT | 按显式数据版本重算差额和缺口，输出建议与假设；缺事实不补默认库存，不自动下采购单 |
| 利润分析 | 对应周期的结算、采购/物流/广告事实、币种/汇率日期、计算版本与缺失项 | 收入到贡献利润的解释、异常 SKU、可检验假设、下一项实验及停止条件；链接机器计算明细 | 结算补账、成本有效期或模型改变时保留原报告并重算新版本；未结算仍是估算 | 解释精确 ReportRun 的变化、核验缺项并提出单变量验证；笔记不代替机器金额事实，也不自动改价/投放 |

来源不足时保留“未知/待验证”和下一负责人；不要把助手总结、静态 CURRENT 或笔记修改时间升级为业务新鲜度。日期应指向源数据的 observed_at/有效期，写笔记日期另列。源修正后保留原版本，用 `supersedes` / “被哪份新记录替代”连接。

## 适合手写的一页结构

以下只是非运行模板，不要求已有笔记换 frontmatter，也不定义新审批格式：

```text
标题 / 状态（观察、假设、决定、经验候选、已替代）
适用范围：精确业务身份、地区/目标、时间窗口
来源：报告路径/ID、代码commit、知识版本、原决定的任务/消息位置
截至时点 / 复核触发条件：不要只写“最新”
观察与缺口：已证实内容和未知分别列出
决定与理由：原决定出处、约束、替代方案、可能损失
验证：实际结果/未运行；对应修复和测试
替代关系：supersedes / superseded_by
负责人 / next_action / 暂停后继续所需输入
```

普通工作笔记先作参考。只有经验已经按现有流程核验并明确适用于某范围，才交给既有来源清单；修改代码/Skill 的规则仍在 canonical 合同中维护，不再建第二份运行规则库。授权与 unknown 处理统一见[线程治理](../THREAD_OPERATING_MODEL.md#authority)。

## 交给 agent 时给一份小包

提供本次目标、明确知识源根/section、相关笔记或快照路径、固定 `knowledge_version`、原评审引用、动态数据的报告 ID/截至时点、当前允许动作和下一交付。无需让 agent 重读整段聊天。任务暂停时保留这些信息；后来导出新知识版本也不替换旧任务的 pin。

现有 CLI 不理解全部 Obsidian 插件或自动解析业务新鲜度，只读取明确 section 的受控 Markdown。完整字段与路径限制见[技术入口](README.md)。`orbit-tool-profile/v1` 是通用工具 profile，不能替代 `product-publication-knowledge-profile/v1`。

## 用现有 CLI 走版本闭环

下面的 `R` 是实际 runtime 绝对根，`P` 是本次明确项目根；命令中的路径替换为真实路径并在有空格时加引号。`knowledge-a.json` / `knowledge-b.json` 分别绑定两份已有评审、精确源字节和 expected_review_digest；`VERSION_A/B` 是相应 preview/export 返回的完整 `sha256:` 加 64 位摘要。清单字段与摘要计算复用[已有格式](README.md)，计算 hash 不产生用户批准。

1. 未评审笔记先只读 inspect。下面示例显式选 `vault/rules`；没有清单返回未就绪及缺口，不会因 draft/CURRENT 字样自动导出。

```text
python R/scripts/sync_product_publication_knowledge.py inspect --runtime-root R --project-root P --vault vault --section rules --source-id fixture-k00b
```

2. 当已有同范围评审及固定源字节时，绑定清单/profile，先预览，再显式导出到 Vault 外的新版本文件。下方 preview 沿用知识卡片的 `knowledge.json` 示例名；运行前将 `knowledge-a.json` 原样复制为同目录的 `knowledge.json`，两者是同一份 A profile，不能省略这个别名文件。演示材料的 review_id、scope 和 instruction_ref 全标 FIXTURE/fixture://，不能移用为真实批准。

```text
python R/scripts/sync_product_publication_knowledge.py export-preview --runtime-root R --project-root P --profile knowledge.json
python R/scripts/sync_product_publication_knowledge.py --runtime-root R --project-root P --profile knowledge-a.json --output snapshots/version-a.json --write
python R/scripts/sync_product_publication_knowledge.py parity --runtime-root R --project-root P --profile knowledge-a.json --snapshot snapshots/version-a.json
python R/scripts/sync_product_publication_knowledge.py inspect --runtime-root R --project-root P --snapshot snapshots/version-a.json --expected-version VERSION_A --query ALPHA
```

3. 来源改变后，用旧清单再跑 parity 会报告 `SOURCE_DIGEST_CHANGED`。保留旧笔记字节和快照；根据对新字节的有效评审生成独立 profile B，预览并导出 B，不能把“改了 hash”当评审。未获得新评审时只完成参考检查，旧任务仍可读取已固定快照。

```text
python R/scripts/sync_product_publication_knowledge.py --runtime-root R --project-root P --profile knowledge-b.json --output snapshots/version-b.json
python R/scripts/sync_product_publication_knowledge.py --runtime-root R --project-root P --profile knowledge-b.json --output snapshots/version-b.json --write
python R/scripts/sync_product_publication_knowledge.py inspect --runtime-root R --project-root P --snapshot snapshots/version-b.json --expected-version VERSION_B --query BETA
python R/scripts/sync_product_publication_knowledge.py inspect --runtime-root R --project-root P --snapshot snapshots/version-a.json --expected-version VERSION_A --query ALPHA
```

把 B 文件搭配 VERSION_A 会退出 2 拒绝，旧任务仍绑定 A。相同文件/内容再次导出为 0 新写，不能覆盖不同旧版本。预览和检索为 0 写，成功导出新版本为 1 写；异常要读取 output_commit_state，UNKNOWN 不代表未写。历史 v1 的明确只读 replay 用法见技术入口。

这组用法在 K00-B 的全新浅 fixture 项目中实际验证，包含卡片原方法、参考项排除、漂移、新旧版本与原文件保全；具体 argv、完整摘要、stdout/exit 和文件前后 SHA 随工单回执交付。`current_execution_authority` 和 `fresh_business_facts_verified` 均为 false。没有因此执行经营实验、更新真实 Vault、自动晋升知识、接通完整业务运行器或完成 APP/U01 集成。
