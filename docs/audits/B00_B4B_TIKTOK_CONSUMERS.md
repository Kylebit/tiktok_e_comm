# B4B 第一段：TikTok 冻结事实消费者

Base `cceda23826442bd71a7262b30b5a19dddeecbadf`。来源为已冻结 r4 的4个tracked渠道/妙手文件和untracked `tiktok_warehouses.py`；本轮对原D只读，source SHA256/函数清单在审计输出 `domain-prep/03/b4b/tiktok-function-merge.json`。不整文件覆盖大publisher，保留当前候选其他函数及B4A的OSError逐目标隔离。

目标子集在验证完整批准快照后才过滤，拒绝空/重复/未批准目标；目标文案、图片和仓库分配从冻结快照投影。仓库始终按精确店铺官方只读列表验证，已批准分配核ID/名称/完整覆盖/总量，旧mainland语义仅用于没有显式仓配的既有模式；别店同名仓不能成为别名。描述SAVE与读回使用同一有序富媒体构建器，SKU规范键确定性绑定，超过平台选项长度在claim前拒绝。

accepted-SAVE只增加读回接缝，不能把任意不同标题作为“平台规范化”放过：只接受已批准标题的空白规范化；规格尾部数量仅在明确的已存在计数单位文本中去重，尺寸数字不是售卖数量。真实r4源对`30 x 45 cm`/`30 pcs`丢数量、对任意非空不同标题接受的两个红例已保留并修复。

9个新聚焦用例：原base8失败/1通过；源函数合入后7通过/2失败（上述源缺陷）；修复后结合3份原测试98通过。原测试fixture补齐新增精确店铺warehouse GET和rich notes合同；所有旧测试函数及原identity/price/零写断言保留，明确option与结构化Size均测。合并期间新method位置错误也被真实测试检出，已修正并检查所有graft函数归属。

本段没有改runner、run账本或HTTP。`target_scope`、`before_mutation`、`before_publish`、`accepted_save_targets`均为直接消费者接缝；第三段将从既有稳定批准run/目标账本接入并验证，**本段不宣称R3端到端已贯通**。没有真实平台/收费/凭据调用，没有服务替换或push。
