# 七个业务 Skill：新 agent / 新项目一页接手卡

本页用于**完整 OrbitHive 源码**的离线接手。先按 [AGENTS](../../AGENTS.md) 和当前 Work Order 核实根、HEAD、工作树及服务身份。`R` 为核实后的完整源码绝对根，`P` 为新项目绝对根，`S` 为本次明确选定的 Skill 安装根。不要从个人安装目录、旧盘符或目录卡推断当前执行权。

## 先看来源和安装身份

```text
python -X utf8 -B R/scripts/inspect_skill_identity.py --runtime-root R --installed-root S --summary
python -X utf8 -B R/scripts/orbit_tools.py --runtime-root R catalog
python -X utf8 -B R/scripts/orbit_tools.py --runtime-root R help CAPABILITY_ID
```

第一条只读比较**全部文件**及 Skill 原文，并显示 copy/junction、resolved target、manifest digest、缺失/多余/变化文件和 registry 校验。`MATCH` 只代表文件一致；`RAW_DRIFT`、`CONFLICT`、`NOT_INSTALLED`、`SOURCE_UNVERIFIED` 都不能当作已安装可执行。先核实 registry digest；若源文件更新而目录清单尚未重生成，保留 `SOURCE_UNVERIFIED`，不要靠个人安装版补齐。上面三个命令均不安装，也不主动访问项目配置或环境密钥；但身份检查会读取并哈希所选 Skill 根内的全部文件，不要在这些目录存放凭据或秘密。这些检查不证明业务 ready。

| Skill ID | 完整源码入口 | 新项目可复用的边界 |
| --- | --- | --- |
| `prepare-product-publication` | [R1](../../skills/prepare-product-publication/SKILL.md) | 精确 Offer ID、完整目标和可信 SKU/包裹事实；零外部写入的候选准备。 |
| `prepare-product-images` | [R2](../../skills/prepare-product-images/SKILL.md) | 冻结的 R1 技术快照、预算、图片来源和付费结果恢复；不能把通用 Lingshi preview 当图片已生成。 |
| `publish-approved-product` | [R3](../../skills/publish-approved-product/SKILL.md) | 完整 runtime、COMMON 技术写入及官方逐字段回读、完整冻结候选的一次终审、逐目标发布回读；registry 标记 `installable=false`。 |
| `delist-products-by-sku` | [下架](../../skills/delist-products-by-sku/SKILL.md) | 精确 Seller SKU/店铺/商品与变体身份，逐店铺退出和官方状态回读；不删商品。 |
| `apply-product-discounts` | [折扣](../../skills/apply-product-discounts/SKILL.md) | 已冻结价格、发布回读和 OneClick 账本；当前默认 writer 范围见原 Skill，Shopee/浏览器能力另核。 |
| `manage-seaya-replenishment` | [供应链](../../domains/supply_chain_operations/skills/manage-seaya-replenishment/SKILL.md) | 当期库存、批次和有效订单；`valid_order` 只算数量，`settlement` 只算金额。重复原始库存身份及过期库存保持 BLOCKED。 |
| `manage-profit-settlement` | [利润](../../domains/data_operations/skills/manage-profit-settlement/SKILL.md) | 官方已结算记录、精确期间、版本化成本/汇率/广告证据；缺历史成本或结算覆盖不能得出最终利润。 |

这七项在 [能力目录](../../config/capability_catalog.json) 均为 `WORKFLOW_RUNTIME_REQUIRED`。复制 `SKILL.md`、通过目录 `help`、或安装文件一致，都不带来原数据、账号、批准、worker 或 provider 能力。妙手 `docs/miaoshou-skills/` 是 `REFERENCE_ONLY`，不算额外可安装的业务 Skill。

## 只预览可移植工具

通用工具的入口见 [工具说明](README.md)。完整源码的 `R/scripts/orbit_tools.py` 可离线 `help`；需项目配置的 `doctor/preview` 使用从 `R/config/tool_profile.example.json` 保存到 `P/profile.json` 的**非秘密**模板，并显式传 `--project-root P --profile profile.json`。例如：

```text
python -X utf8 -B R/scripts/orbit_tools.py --runtime-root R --project-root P --profile profile.json doctor --capability lingshi
python -X utf8 -B R/scripts/orbit_tools.py --runtime-root R --project-root P --profile profile.json preview lingshi media --payload request.json
python -X utf8 -B R/scripts/package_agent_tools.py --runtime-root R --destination NEW_PACKAGE
```

最后一条默认只预览固定白名单的 `orbit-portable-tools/6` 包；`--build` 才写入全新目录。该包只含三个 portable Skill、有限知识入口和关闭客户端源码副本，**不含七个业务 Skill、完整发布服务端、凭据或业务数据库**。`doctor` 只检查本地文件/依赖/环境键名，`preview` 只形成离线计划；真实读取、付费、安装、业务写入分别按原 Skill 的授权和回读合同处理。

## 两个当前 HOLD

- 个人 `manage-seaya-replenishment` 是指向旧 `04-supply-chain-ops` 的 junction。旧版对相同数量的重复库存行可计一次；工程版没有来源库位身份时一律 `BLOCKED_INVENTORY`。先保留 junction 与完整 manifest，不能自动覆盖或用旧规则算补货。
- 历史 `skills/publish-approved-product/scripts/publish_approved_product.py` 仍有旧 `C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm` Python 回退路径。它及直接 `dispatch_*.py`/`readback_*.py` 只供历史诊断，不能用于新商品的发布入口，也不随 portable 包分发。新发布按 R3 所述的已核服务与 `product_center_publication.py` 执行。

安装检查使用 [接手指南](../AGENT_HANDOFF.md#浅目录-skill-check) 的 `--check` 形式与显式 `S`；检查失败不自动安装。若需迁移个人版，先保存 resolved target 与完整 manifest，单独审查冲突及恢复方案。任何文件或服务身份变化后重新跑上述只读检查，不把本页某次状态当永久结论。
