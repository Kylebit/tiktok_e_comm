# 源码交付分支

此分支从 GitHub master eb60a905 建立，组合本地冻结平台 16d78564 的明确源码白名单；后续首页修复来自 dfb372b8。未导入本地961条祖先历史。

仅代码、必要文档、示例配置和离线合成 fixtures；正式 artifacts/reports/data/runtime、SQLite、原始付费/结算响应及私有月报不交付。b4b 名为 actual 的 fixtures 为真实 producer 通过合成事实和 fake 最底I/O产生，PROVENANCE 已记录；不是实际平台响应。供应链 captured 输入/原 July 报告需由受控部署配置外置绑定，缺少时显示 unavailable，不能从此源码 clone 推断业务就绪。

本分支未合并 master、未部署。不得把测试 fixture 视为业务授权，执行依赖固定 runtime 与本次真实 inputs。
