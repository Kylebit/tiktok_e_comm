# 独立内容页面退役

按用户已确认的减法范围，移除 `/titles`、`/images`、`/localized-image-review` 及其 `.html` 页面，删除专用页面和 localized 页面脚本。撤销 `/ai-image-studio`、`/ai-images` 独立别名和目录入口；旧链接返回 404。

后续核验发现 `/new-product/images` 仍直接加载旧 `ai_image_studio.html`，但商品发布中心的图片导航、来源图选择、图片结果和多语言结果均在 `/product-workspace` 页内，源码没有指向该旧路由的调用。因此 `/new-product/images` 及其 `.html` 别名也已退役，返回 404。图片 Skill 与发布工作流 API 保持不变；旧图片静态资源和历史资产尚未删除。

此次补充退役的路由、原审核页 CSP 等隔离 HTTP 回归为 15 项通过。扩大到统一入口与代理安全的 71 项时，63 项通过、8 项失败；失败涉及既有 `/profit` 状态、首页重定向与服务身份依赖，不属于此次路由改动。初次退役的 55 项回归及其旧断言失败记录仍属历史回执，不能据此声明当前整个工程测试通过。
