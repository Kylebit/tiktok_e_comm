# B4B 折扣只读入口配对修正

本提交以 `2e23d61170a0b5e2b84d01bdfc7d62565c062638` 为父项，在新独立 COMMON 树先修
折扣读取入口；尚未加入 COMMON 实现。父项不能单独视为全部折扣辅助工具可用。

根复核确认 `audit_existing_discount_evidence.py` 仍导入已退役 executor 中删除的
`_shopee_discount_items`，此前仅 AST 编译未发现。迁移冻结 r4 的只读 `get_discount`
分页 helper 到真正 reader 内，使用现成 `shop_get`；不恢复 writer、token 导出或第二账本。
仅接受明确布尔分页标记，拒绝非对象页、异常响应、重复 item 身份、空页仍声明后续页及
超过 1000 页。端点来自冻结源码合同；本包未联网重新查询官方当前规格。

根同时指出本地模型缓存只用 `(shop_id, model_id)` 会跨商品串 SKU，且缺 model 会嫁接
商品唯一 SKU。实际合成数据库/最低 GET 红例输出 SKU-B/C/Y，错误丢失 SKU-A，证据保留。
现改为 `(shop_id, item_id, model_id)` → SKU 集合；只有唯一明确绑定用于参考。缺失、冲突、
畸形 model 进入 `identity_gaps`，不构造 SKU、不复制比例补缺事实。读取数据库使用 mode=ro
且关闭连接。价格来自官方折扣返回，SKU 绑定来自本地缓存，因此明确标
`OFFICIAL_DISCOUNT_LOCAL_MODEL_IDENTITY_REFERENCE` / `LOCAL_CACHED_SHOP_ITEM_MODEL`，
不冒充新近官方 SKU 回读；整个报告 `approval_status=NOT_APPROVED`。

给 reader 增加 `--source/--output/--database`，给图片相似性参考脚本增加
`--official/--miaoshou/--output`，`--help` 在读取报告/数据库/配置之前返回。原默认路径保留，
不触碰个人安装。两份 canonical SHA 与来源处置已在原 manifest 更新；11 个 Skill 路径数不变。

验证：

- 首次实际导入/入口集合 12 中 6 红：主要为同一断裂 import 的级联，另一个脚本 help 误读
  历史默认报告；不是 6 个独立缺陷。
- 先只修 import/help，实际 reader CLI 数据模型用例仍红，保留 `discount-reader-identity-red.xml`。
- 最后 `discount-reader-cli-green.xml` 43 pass/0 skip，包含新 12 项和前一折扣窄集相关 31 项。
  8 个 Python 入口逐一真实 import + `runpy.run_path(..., run_name='__main__') --help`，配置
  读取替身会主动失败；3 个非 Python canonical 文件已读、不是可执行脚本。
- 真实 reader CLI 使用临时只读 SQLite、2 页最低 `shop_get` fake、合成 token/shop resolver；
  同 model=7 的两商品分别留 SKU-A/B，未绑定/冲突/缺 model 的三商品保留缺口，DB 字节不变。
- 所有检查先装 Python socket连接/发送/DNS/子进程拒绝与空配置缓存；没有真实平台、收费或
  凭据读取。该保护不是 OS 防火墙；历史 B4B test-stable 事故记录继续有效。

此配对修复不等于 Shopee 折扣 writer 已接通，不修改父项的促销账本/百分比批准合同。
