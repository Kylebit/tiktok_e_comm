# 旧运营页面退役

本包移除用户截图中的 MX/UK 历史审批、旧促销、旧下架页面及其导航，同时撤下 Ozon 旧运营台和未实现达人合作的发现卡片。四个页面及 `.html` 入口返回 404。

本包完成页面层清理；旧审批、促销后台 API 与命令行依赖仍待下一包清理，不能将隐藏页面当作整个后台功能已删除。现代上架、精确 SKU 下架、发布后折扣 Skill、Ozon 平台适配器及共享业务数据仍保留。

后续 API 包已删除 `/api/mx/`、`/api/uk/`、`/api/promotions`、`/api/deactivate` 的旧 HTTP 执行分支，删除 MX/UK 发布线程与旧首页审批数量读取。旧 POST 路径在处理数据前返回 404。命令行及历史审批数据模块仍待分别处理；`delist-products-by-sku` 实际调用 `modules.products.deactivate.push_deactivate`，此共享底层必须保留。

CLI 包进一步移除达人占位功能、旧促销与零销候选 CLI、标题页启动命令和已删页面启动选项；删除专用 MX/UK 网页审批模块与无人调用的迁移派发脚本。历史 JSON、人工定价读取、现代发布/折扣/下架 Skill 和共享图片生成代码未删除。

Ozon UI 包删除旧运营 HTML 和专用迁移 JS；主控制台 `/ozon`、`/rus` 不再跳往旧运营台，独立服务页面返回 404。健康检查与正式 API 保留：账单页实际使用 Ozon `settlement_summary`，商品目录与发布也依赖平台适配层。旧 `feishu.ozon_data_dir` 仅作为历史路径读取兼容，不包含飞书连接、通知或发送功能；新配置与错误提示统一为 `ozon.data_dir`。未修改任何用户设置或现有数据位置。
