# B00-B4B Shopee / Ozon 消费者整合

本提交接在 TikTok 仓库修正 `d808c70944ae0beecbdd07bc988485fbe0d0517e` 后。它合入冻结 r4 的 Shopee global v1/v2 图片、类目、地区完整描述图及 Ozon 库存回读消费者，保留 C2/B4A 已有发布身份和全部 Ozon 变体。R3 compiler / runner 最终接入在后续提交，本提交不代表整个 B4B 完成，也没有服务切换。

## 行为

- Shopee MY/TH/VN 必须使用快照中已有的目标语言文案。global resolver 在第一个 provider 调用前检查；地区创建和已存在商品回读也检查。缺失时提示现有 `prepare-product-publication` / `prepare-product-images` 准备流程。R3 不再自动调用 ToAPI 或 Lingshi 翻译；旧任务中的 provider 身份不改写。
- 旧 ReleasePlan 仅有 CNSC 英文，不能证明地区文案已冻结，因此 MY/TH/VN 的旧 adapter 返回 `FROZEN_REGIONAL_COPY_REQUIRED` 和零写入。旧 HTTP 退役保持，PH 英文合同和只读诊断保留。实际临时 ReleaseStore → approve → start_run → adapter 负例验证付费调用 0、平台 mutation 0。对应完整冻结文案的 v4 修复仍可执行，且用实际第二次 GET 判断是否匹配。
- 图片上传按实际 HTTP mutation 计数。global 缓存命中 0；缺两张图 + create + init 为 4。区域每张图片下载后、上传前 reserve；下载/本地预算失败不把未发送请求记 UNKNOWN；已发送但超时或响应无 image identity 保留占用和已完成数量。后续 copy/description/list 预算失败保留此前已写计数。
- Ozon 的相同类别/类型展示文案不再覆盖祖先 ID 冲突；真实三变体消费者中错误祖先 0 次 import，合法相同 ID 路径仍 3 次。保留已治理库存读→一次更新→读回与既有特殊平台处理。

## 验证与来源

运行本包 `offline_pytest.py` 的 8 个受影响测试文件，`shopee-ozon-segment-final.xml` 为 **246 passed，0 skip**。新增消费级负例为 `tests/test_b4b_shopee_ozon_guards.py`；区域预算红例为 `regional-budget-red.xml`。旧测试中通用价格/畸形回执/重复发布用 PH 冻结英文合同保留断言；旧自动翻译测试明确改为迁移响应，已冻结文案的原地修复由 v4 地区测试覆盖。

冻结来源和逐函数差异位于审计输出 `domain-prep/03/b4b/source-inventory.json`、`shopee-ozon-source-merge.json`、`shopee-ozon-test-merge.json`。新增 `modules/shopee/publish.py` 仅 `_upload_images_exact` 直接回调接缝，未整文件覆盖。Ozon 的 category_match/listing_text/migrate_batch/product_lifecycle/release_variants/target_scoped 六项已证明当前与 r4 / D 来源完整 LF 内容相同，无额外覆盖。

## 测试隔离异常必须保留

早期合成旧 adapter 负例曾漏替换已导入的 `shop_post` 别名，导致对 Shopee test-stable `POST /api/v2/product/update_item` 一次可证逻辑请求，官方返回无效 Partner_id 拒绝。使用的是空合成配置，没有读取真实凭据；实际 wire 次数未记录，未取得 provider 审计。因此本包不能称绝对零出网。原证据保留在审计输出 `TEST_ISOLATION_INCIDENT.md/json`、`shopee-ozon-red3.xml`。

之后运行的 launcher 在 pytest import 前阻断 Python socket 连接、发送、DNS 解析及子进程事件，并预置空合成配置；它是本组 Python 调用路径的隔离，不是 OS 全局防火墙。上述 246 项及其 fake mutation 计数与早期异常分开，未再调用官方接口补证。测试不能证明真实上架、实际活动权限或店铺状态。
