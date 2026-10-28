# B4B TikTok 首段必要修正

本提交必须与 `55084447fe055cca79e2bc6b87b69f2be0ee8bbe` 配对使用。

两个实际仓库消费者现在只接受显式整数 `1` 或字符串 `"1"` 的有效状态；`0`、`False`、`None`、缺失字段都不能选作有效仓库。旧代码的 `or "1"` 不是成功响应证据，因此没有将未知状态兼容当作已知合法合同。店铺列表必须明确给出对应 shopId；重复的有效 warehouseId 会在构建 dict 前拒绝，包括不同名称的重复行。既有明确有效的字符串/数字状态、已批准完整库存和当前精确店铺读取继续通过。

规格 schema 的 `validate_specification_mapping` 允许独立的纯数字数量值；例如 quantity=`30` 与 size=`30 x 45 cm` 合法。消费者现在只在带明确计数单位的文本已存在时去重，去掉会吞掉纯数字数量的 substring 分支。未增加翻译或重新推断数量。

验证：`tests/test_b4b_tiktok_warehouse_identity.py` 的 20 个 case 直接经过真实 `prepare_save_draft` / `save_prepared_draft`、`TikTokPublisher.publish` 和 `read_draft` / `save_approved_draft`；最低 provider boundary 为 fake。13 个失败红例保存在审计输出的 `tiktok-warehouse-red.xml`，修正后原相关集合 118 passed，增加完整 SAVE 字段断言后窄集 20 passed。合法状态对照证明库存仍进入既定准确 shop/warehouse 映射，数量保留于实际 skuPropertyList 和 skuMap.specification。

本组使用审计目录的进程级 Python 禁网 launcher，禁止 DNS、socket 和子进程调用。它是本组 Python 调用路径隔离，不能当作操作系统全局防火墙。本组无真实平台或收费调用；B4B 另一次 Shopee test-host 隔离异常由独立 TEST_ISOLATION_INCIDENT 记录，不应隐去。
