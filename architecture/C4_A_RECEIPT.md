# C4-A 前端与内容安全基线组合回执

四项已验收前端提交已组合到 C3 + S02-C 精确基线上，无冲突；额外只更正两行泛化重复批准/付费接线说明。未改内容核心、未整合 B4B WIP。发现一次旧锁文件初始化并发失败，虽单项复跑通过，仍保留为未解决 S02-D 问题，不作为完整 APP 发布通过。

## 精确身份

- 根：`C:\Users\Windows11\Desktop\Agent_PR\outputs\orbithive-c4-frontend-20260905`，分支 `codex/orbithive-c4-frontend-20260905`。
- base：`6b920cdf83b1275370537f5d3ab17269fad5a0bf`；其 parent：`b0b40072d8205879bce31c1ce90c40b562a42e62`。
- 实现 HEAD：`86c0874c9315f47db688f12b688f474d17516847`；parent：`730acce725ea4062d082ae27efd3436ec1842765`；提交后 clean。
- 此回执为下一独立文档提交；最终 SHA / parent / clean 与预览最终 PID 记录于 `C:\Users\Windows11\Desktop\Agent_PR\outputs\orbithive-audit-20260905\architecture\c4-validation\finalization.json`。其变更仅本 MD/JSON，不用自引用 SHA。
- 原 U00、D00 8457 检查点、视频独立工程与既有预览全部保留。没有 push、部署、安装、生产配置/库读取、平台/auth/付费请求或媒体/依赖复制。

## 重放与来源处置

| 原提交 | 本包重放提交 | stable patch-id |
| --- | --- | --- |
| `052ed7433399575ec82434fa3ef7f0511e5fbfe5` | `5439dfe35002ff4a4fd33e15c684fe25d30f6284` | `7c633220b4faec7f882aea244338abca84000854` |
| `8457cf2246c7520bade525957cba2893a9bf0526` | `69b259af2790100075c21224ea62eb97c4f6e3d3` | `0c7594d5af5c898638245b15ce8f1d5bacee7659` |
| `f634ab9937971d24f80904c8340dce7dfef92afb` | `d8c8fed9d9bf2baa3170d9c563302e73893c89d3` | `e062505ca7ee9a85614e55642a20b43b7061a93c` |
| `cfbdcd218396e646fe3effeca5c83fb4938422f9` | `730acce725ea4062d082ae27efd3436ec1842765` | `4058d445fd7e74a2b91f8ed29a967ac782d8f31d` |

四项 patch-id 逐一一致，源路径集合与 C3/S02-C 自 C2 以来差量无重叠，未发生三方冲突或整文件 ours/theirs 取舍。额外 `86c0874c9315f47db688f12b688f474d17516847` 仅修改 `web/product_workspace.html:242`、`:582` 两行用户文案，复用当前冻结范围有效批准；没有删除或增加批准条件、付费上下文、目标执行 guard。

`preflight.json` 保存5个来源/检查点 exact HEAD/clean 与原 patch，`resource-preservation.json` 保存重放映射及1180个未变 base 文件的 Git tree 摘要。既有 C2/R00 不重复合入，B4B 与 r4 不读取并行 WIP 或整树覆盖，视频 Sites 项目不嵌入媒体/依赖。

## 资源与消费者保全

224 张供应图片物理 SHA-256、13 项供应同步清单 CRLF→LF SHA、R00九文档规范化 SHA/Git blob 均核验一致。S02-C `image_generation_checkpoint.py` blob `b3a9f5339dfa6ff2f4b88590250a64cf105be86e` 及微秒测试保持基线原对象；C3预算、源绑定与 UNKNOWN 消费者不改。

当前 C4 `modules/products/server.py:604` 和 `modules/products/release_adapters.py:350` 仍调用 `modules/shopee/global_copy.py:314` 的旧英文 fallback；来源标记仍为 deterministic_verified_facts_fallback。B4B 的退役尚未组合，不能宣称当前已退役；此为后续 B4B 源/消费者/UI 同步项。没有为去除文案而改其业务实现。

当前 `skills/prepare-product-images/scripts/prepare_product_images.py:2155` 的真实 CLI 会加载 paid_context 并核验范围/策略；brand 和 translation 消费者分别在 `modules/sourcing/brand_image_lingshi_generation.py:152`、`localized_image_lingshi_generation.py:175` 保留身份门禁。当前浏览器商品页没有 paid_context producer，因此文案明确流程尚待接通，不把代码/Skill 存在当作已可付费执行。精确源 SHA 与行号见 JSON source_contracts。

## 实际验证与失败台账

| 集合 | 本次结果 | 证据 |
| --- | --- | --- |
| U00真实handler / launcher / 独立进程启动快照 / profile / source变更 | 73 passed | `entry.xml` |
| S02-C时间、ownership、UNKNOWN、实际内容消费者 | 首跑102 passed / 1 failed | `content.xml`、`content.log` |
| 仅上项失败，用新短fixture重跑 | 1 passed；不视为修复 | `content-retry.xml` |
| Chrome高密度商品页 + 统一入口 wrapper | 2 passed | `browser.xml` |
| 高密度真实浏览器内部检查 | 103 ok | 审计 `c4t-browser/t0/browser/browser-result.json` |
| 统一入口双尺寸/记录集合/运行身份 | 26条实际记录 passed | 审计 `c4t-browser/t1/browser-result.json` |
| 隔离guard负向探针 | 5项动作前阻断 | `negative1.json` |

去重后178项最新pytest结果为 passed，0 skip；这个数字不能抹去首跑并发失败。未重复已绿102项，也未重跑未变的665或整仓。测试执行的四重放 HEAD 为730acce7；随后两行低影响文案由 root 在86c0874真实页面和完整diff复核，不重跑整组。每次完整参数、cwd、runtime、基线、短 basetemp、imports、JUnit 和日志均独立保存。

锁失败在 `tests/test_lingshi_checkpoint_recovery.py::test_process_concurrency_and_crash_released_kernel_lock`：两个真实worker退出 `[0,1]`，第二个在未改业务文件109行 `stream.flush()` 报 PermissionError。测试把worker输出放入断言，pytest截断了部分原stderr，未单独保存完整原流；不虚构补全。退出后锁文件 size2 / hex3030 / SHA f1534392279bddbf9d43dde8701cb5be14b82f76ec6607bf8d6ad557f60f304e，只是退出后状态，不是实时调度证据。write/flush 在kernel lock前是候选竞争解释。完整边界和两次命令见 `lock-initialization-observation.json`；root已准备独立 S02-D，C4未擅改核心。

## 隔离与新预览

测试导入前安装由已审 C3 guard 改出的审计 sitecustomize。子进程按具体source hash/参数放行；loopback只允许本包真实bind及具名preview peer。SQLite解析 file URI 后限定内存或本包合成根；旧v1兼容目录只有本次创建才登记。Node浏览器脚本也限定显式fixture端口，页面路由拦截外域。真实8765/8766、3000/8788、旧U00与视频端口禁止访问。

5个负向探针对真实旧端口、外部DNS、越界SQLite URI、未具名子进程、合成越界配置均在动作前阻断；实际测试集合的 guard blocked 为0。不能把负向探针写成0尝试。guard是进程内测试防护，不是系统防火墙或生产服务验收。Python只借用D盘venv运行时，所有应用imports均指向C4。

独立预览 `http://127.0.0.1:49289/product-workspace?offer_id=99003`，回执前PID15364。它运行真实handler和ReleaseStore，所有商品/批准/图片/目标回读均合成；POST403、未接入API503。配置缓存手工绑定合成路径，未调用真实配置初始化，未连接真实数据库。health READY保持 identity_only=true / business_execution_verified=false；独立checker用错误profile实际得到CONFIG_MISMATCH，见 `identity-before.json`。

root已在CUA tab9看到Offer99003/revision7/TEST-plan匹配、2张真实400×400合成图、1完成/1待对账/1未执行账本；TikTok/Shopee禁用，Ozon待执行保留，无写按钮点击。布局和两行授权说明已独立查看。原生WebView不在本包。

回执提交后会先保存旧PID15364的SOURCE_MISMATCH，再只重启本包fixture到同一49289端口，保留旧/新PID和最终identity核验；最终 `identity-final.json` / `finalization.json` 为最终源指纹与进程记录。原U00 PID63228/64568、视频PID56008和真实服务均不停止。

可复跑入口为审计 `run_tests.py entry`、`run_tests.py content`、`run_tests.py browser`（每个新运行应使用唯一label，不覆盖现有证据；具体参数见*-command.json）。稳定fixture由 `run_preview.py <owned-port>` 启动；不得直接运行生产launcher或使用原服务端口。审计根：`C:\Users\Windows11\Desktop\Agent_PR\outputs\orbithive-audit-20260905\architecture\c4-validation`。

## 下一步

root审查并接受本包精确最终提交后，先激活独立S02-D修复已观察的锁初始化竞争，再以其验收SHA更新D00。旧8457 D00树仅是clean检查点；C4和最新pytest结果不等于完整APP或系统安全发布验收。
