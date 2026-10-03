# 商品目录更新治理

本页定义目录变更原则。旧同步实现风险、Offer 3828540231 与 0946/0952 的时点记录移到[历史参考](LEGACY_PLATFORM_REFERENCE.md#catalog-history)；它们不是当前目录、可用 SKU 或现存缺陷的结论。

## 输入、预留与变更集

- 冻结当前与候选快照的来源、平台/店铺、revision、完整性和 checksum；缺失/空/部分响应不能被解释为远端全部删除。
- 按正式产品/SKU 合同识别对象，区分 source identity、offer、变体和平台目标；不能只按尾四位合并不同来源或命名空间。
- 预览 add/update/remove、字段差量、预留冲突及各项 blocker。删除须有完整输入证据并在已批准变更范围内，不因用户批准“同步”推定无限清空权。
- SKU 分配消费当前目录与正式 reservation/批准状态；迁移时还需核对尚未迁移的有效 legacy locks/claims。持久化唯一性与并发/重启合同以当前实现验证；冲突不靠历史“下一个号码”或硬编码 Offer 绕过。

当前预览入口为 [catalog_update_preview.py](../domains/product_operations/catalog_update_preview.py) 的 `preview_catalog_update`，其 preview payload 的 `dry_run=true` / `apply_allowed=false` 不等于执行许可。代码存在不证明所有调用方已经接通。对照入口包括 [preview 测试](../tests/test_catalog_update_preview.py)、[同步安全测试](../tests/test_catalog_sync_p0_safety.py)和[同步治理测试](../tests/test_catalog_sync_governance.py)，执行范围按本次改变确定。

## 应用与验证

1. 按[数据库治理](DATABASE_GOVERNANCE.md)获得一致且可恢复的备份；冻结完整远程输入，抓取期间不通过破坏性写入拼装“候选”。
2. 对冻结差量和预留运行质量/身份检查，把实际变更范围绑定可审计批准；授权复用规则见[线程治理](THREAD_OPERATING_MODEL.md#authority)。
3. 在当前正式存储合同中以事务/唯一约束和持久并发控制保护写入；不额外开第二套预留账本。声明跨平台一致性须有各数据源的版本与提交证据。
4. 记录 run、操作者、批准、输入/输出版本、差量、尝试/结果及恢复引用。异常回滚不丢失原快照；逐目标/店铺失败不伪报全量成功。
5. 写后核对完整性、业务差异与正式回读；未决或失败状态留证据。必要恢复依已审范围执行，不自动对全部平台重跑。

实现缺口须在精确候选用相关回归定位，记录代码 owner 和下一动作；不要把旧文档风险直接冒充当前红例，也不要因已有测试文件就声称生产更新安全。本治理整理没有执行真实目录同步。
