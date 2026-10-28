# B00-B4B R3 合同接入（阶段提交）

基线为 `f2f71491ad63668e38f7576d205e2f1dc01f8245`。本提交保留 B4A 原 run/target claim 账本、UNKNOWN 阻断、旧写入口退役及 S01 安全边界，不代表 B4B 全部 46 项已经完成。

## 来源与行为

- `publication_autopilot.py` 来源为内容 owner 正式交接的 `01e58ce6b4d9b0e460656b6e7731aba10135922a` 精确 blob。保留 R2 policy 的付费上限、用途、authority 校验；不创建或启用默认 ACTIVE policy。
- `publication_quality.py`、`publication_quality_evidence.py`、`publication_preflight.py` 及两个 product-family JSON 来源为恢复集 r4。来源 SHA 记录在审计目录 `r3-read-contracts-source.json`。质量两模块与两个数据文件保持来源内容；preflight 增加真实 domain digest/schema 验证及合法 Shopee v1 绑定兼容。
- compiler 验证真实 approved snapshot，保留 v1 的零基 `position` 与 `image_url`，不冒充 v2 字段。按完整快照验证仓配，再仅投影当前平台/目标；库存 review 明确每个 model SKU 的数量，避免误读成所有 SKU 总量。
- 编译所需 HTTP mutation 预算不抬高已批准 policy 硬上限。Shopee 缓存图片上传为 0，缺失图片逐次 POST 计数；共享 create/init 与区域动作分别受限。
- server 和直接 runner 按持久的 offer/plan/snapshot/实际平台目标集合解析最终候选。无该关系的旧有效 v4 批准保留；已有关系不能通过省略请求字段绕过最终批准或 ledger。唯一有效最终批准可自动复用，新 pending 不撤销原有效批准；多个有效批准需要明确 digest。损坏、漂移或越界文件不回落旧路径。后台在进入 runner 时再次校验所选冻结 packet。
- 报告 v2 增加兼容可选 `mutation_budgets` 与 `release_authorization`，原历史 v1/v2 无此字段仍可读；SQLite schema 不变。
- Ozon 本地预算拒绝保留已发送前缀的事实与计数，未发送后缀用 `ozon_variant_suffix_not_attempted` 标记；UNKNOWN 前缀保持 PROCESSING，原 run 读回不重发。库存写前本地拒绝用 `ozon_stock_not_attempted` 保留之前 import 计数。

## 回归与实际边界

审计目录 `domain-prep/03/b4b/r3-core-green.xml`：70 项通过、0 skip/0 fail。包含真实 domain snapshot/真实 ReleaseStore、真实 server start→后台→runner→报告持久化、真实 Ozon executor 与最低层假传输、compiler→ledger→Shopee checkpoint，以及旧 runner/report/composition 回归。server 授权测试的 executor 为零写假执行器；Ozon import+stock 验证通过真实 executor 直接装配，不把两者混称默认 live server 全链完成。

`r3-omitted-candidate-red.xml` 保存省略字段导致绕过审核/ledger 的两个真实红例；`r3-authority-path-red.xml` 保存自有合成 fixture 文件符号链接越界的两个红例；`ozon-partial-red3.xml` 保存真实已发送前缀计数丢失红例。后续测试通过审计 launcher 的 Python 进程网络隔离运行。

本包早期曾发生一次官方 test-stable Shopee 逻辑 POST 外连，不能称整个 B4B 出网为 0；精确端点、拒绝、次数范围和未证实项见审计 `TEST_ISOLATION_INCIDENT.md/json`。未读取真实凭据，后续无真实平台写验证。

## 后续仍需完成

1. 默认 live 装配的 TikTok 准备 ledger/target_scope 与 Ozon stock 方法接缝仍待下一阶段提交和最低 HTTP fake 验证；本提交不宣称默认服务端库存闭环。
2. 本包尚未提供用户可执行的“仅未发送 Ozon SKU”新候选恢复。未来必须保留完整 snapshot/reservation，将每个 SENT/UNSENT 证据、前序报告 digest、执行 SKU 范围、精确目标、新预算一起冻结/批准并在 dispatch 前复核；UNKNOWN/进行中不允许新 dispatch。
3. R3 技能/脚本、折扣与其余来源语义处置尚未随此阶段提交完成。完整 S02 image bridge 组合验证亦待后续，不能称图像到发布 E2E。

## 配对路径修正

`r3-persistence-path-red2.xml` 记录五项实际目录符号链接红例：两项现存根外目录会被写入，两项断链接抛出未分类文件异常，一项断链接候选目录会被当成不存在而回落旧合同。持久函数与解析器现均在 exists/读取/mkdir/open 前执行同一边界守卫；`r3-persistence-path-green.xml` 的 23 项批准与路径测试通过。此修正必须与本阶段配对消费。验证使用自有合成目录，不触及业务或秘密文件。

持久函数仍采用原有 `open('x')` 独占文件合同，进程中断可能留下半写文件。损坏记录现在阻断执行并报告缺口，不自动覆盖、重批或回落旧合同。恢复需保全损坏文件并依据原 digest 与完整候选/批准证据修复；原子落盘改进作为后续窄工作，不在本包另建账本。路径守卫不宣称抵抗有本机写权限的攻击者在检查后并发替换目录。
