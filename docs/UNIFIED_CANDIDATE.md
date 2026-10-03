# 统一工程候选

本候选来自已验收 `2921685aaf7f5611a5f14e1a152a5d888f1dc55a`，继承现用 `dea30b5` 全部业务代码，另含已验三个质量负例和四份中文文档同步。没有移植旧UI、旧执行配置、未验利润改动或个人授权。

## 启动与数据

本机候选目录：`D:/OrbitCandidates/OrbitHive-20260908`。独立演练证据和配置：`D:/OrbitEvidence/unified-candidate-20260908/preview`。数据副本位于其 `private/catalog-copy.db`，运行态位于 `state`。启动命令以该目录 `restart-LAUNCH.json` 的精确 command 为准，先核端口无占用再启动，不启动第二个进程。当前受控启动入口为本候选 `scripts/catalog_review_preview.py`。

该入口只读演练使用独立目录副本；成本保存若手动触发只影响副本，平台动作被阻断。演练本身仅发GET，不复制真实执行配置。现用49289的数据库、成本override及服务保持不变。

成本override目前代码仍从代码根 `data/weight_overrides.json` 读取，因此演练保存一份受控忽略副本并hash核对；此文件未版本化。它是已知尚未完全外置的运行数据，不能将干净checkout误称无需部署配置。利润/供应链完整业务数据未接入此只读预览，不以页面打开证明全业务可执行。

## 来源与目录决定

候选仍是验收侧common Git的登记worktree，Git对象归属原common目录，不能删除那个目录。演练暂使用正式树的Python虚拟环境；源码加载使用本候选根，Python依赖环境尚需在最后目录迁移时重建并验证。这两项是具体保留依赖，不宣称完全独立安装包。

建议最终采用 `D:/OrbitHive/repository` 独立Git仓库、`D:/OrbitHive/runtime` 受控运行数据/配置、`D:/OrbitHive/environments` Python环境、`D:/OrbitEvidence` 验收证据。最终目录切换另作用户审核；本包不搬移或覆盖旧树、不推送。

## 验收清单

- 精确HEAD、与现用代码diff、Git状态与来源可核。
- 独立端口health指向候选与副本；重启后身份保持。
- 商品目录339个内部SKU，0958–0982共25恢复项，统一成本读取一致；既有分页与知识资产保持原blob。
- 原库及override前后hash/表指纹一致；没有正式平台、认证或源SQL写。
- 远程仅核已有名称与本地来源；tracking时效未知，不fetch/push，不输出带凭据URL。
- 独立审查后才称候选验收通过，现用与最终目录切换均未执行。
