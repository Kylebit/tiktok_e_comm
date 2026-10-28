# B00-B4A：发布入口版本与幂等边界

精确底座：`6d6de7799140beb8c65e3dff516f61331d78e2ed`。仅此隔离包，不代表全部 B4/S03 或整个 OrbitHive 已完成。

## 真实问题及修复

七个 collectbox HTTP 用例只创建旧 ReleasePlan 和旧内部 draft identity，却调用新版 `/api/product-workspace/publish-tiktok`。真实 runner 找不到 v4 批准快照而返回409是正确门禁，不能通过去掉验证或改回旧写路由解决。七个原 fixture 保留，改为零写迁移断言；新的跨层用例验证真实 v4 批准、六店、缺证、类目超时、UNKNOWN、回读和重入。

当前旧 `/publish` 仍进入 `_start_tiktok_release`，与已有退役文档相冲突。本包将这个 HTTP 写路由改为410、明确新入口和需要重新批准的 v4 schema；历史 GET 状态路由不变。旧 fixture 的实跑为两次HTTP200、0次模拟 publish（当前发布器拒绝缺少 expected_title），这不是已证明的旧入口重复外写。

新 v4 入口真实复现：同一批准请求顺序发送两次，正常、单店UNKNOWN、全店UNKNOWN三组均产生12次模拟 publish。原每次随机 run_id 不能按批准请求去重。现在以完整批准快照摘要、商品、plan、平台、目标集合和显式 retry 链构造稳定请求标识，并在现有 run 账本 `BEGIN IMMEDIATE` 中查找及登记。随机旧 run 也按其完整已存身份识别。没有第二账本或全局业务锁。

稳定 run_id 保持旧32位十六进制尾部长度，完整身份仍由账本校验；截断键冲突只能失败，不能授权错配执行。执行代码或 Skill 摘要变化不会生成新的批准身份。重复请求返回原run/report和202、`reused:true`，不会再次启动后台执行器；登记后崩溃、提交中、COMPLETED但平台仍PROCESSING或UNKNOWN均适用。

单店类目读取 `TimeoutError` 原会逃出单店处理，造成整批失败；现在该局部捕获 `OSError`，其余就绪店继续。持久 v4 快照 schema、digest、target 损坏原会导致 HTTP 连接中断；现在将 Store 的 `ImmutableReleaseError` 转为409及本次0外部写，校验继续生效。

## 消费者与迁移

| 消费者 | 当前合同 | 本包行为 |
| --- | --- | --- |
| `web/static/product_workspace.js` 的四个发布按钮函数 | 三个 v4 平台路由；`currentReleaseBody()`；202后轮询报告 | 原样支持，目标必须与已冻结全集一致；目标顺序不产生新请求 |
| `skills/publish-approved-product/scripts/product_center_publication.py` | `{offer_id, plan_id}`；三个 v4 路由；`product-publication-start/v1` | 原样支持；重复启动返回原报告 |
| `skills/publish-approved-product/SKILL.md` | 上述脚本是生产控制入口；旧dispatch脚本已标deprecated | 不更改已安装Skill或镜像清单 |
| `dispatch_tiktok.py` 旧诊断脚本 | 旧 `approved_request`，可带 `tiktok_target_scope`，仍指向新路由 | 缺少v4批准/旧scope被明确拒绝；不能把诊断脚本当生产入口 |
| 旧 `POST /api/product-workspace/publish` | 已过现有2026-09-01退役期限 | 410；无自动迁移、无自动重放；历史GET仍保留 |

旧聊天批准、旧plan批准、已导入妙手或旧ACCEPTED均不自动转换为v4批准。迁移须重新核定实际商品、SKU、版本、平台/店铺、内容、价格与证据，经现有批准流程产生新的不可变v4快照。

## 恢复与目标范围

- 同一批准的普通重复请求始终读回已有run，不隐式重试。
- 显式请求可带 `retry_of_run_id`：必须指向同一商品/plan/快照/平台、已COMPLETED的FAILED报告，并且 `dispatch_attempted=false`、`external_write_count=0`。这是零写失败的重试，重发该retry请求仍复用同一run。用真实类目故障→恢复的最低传输验证后可执行六店一次。
- 仅修改请求里的目标子集不构成批准：返回409；修改顺序仍是原批准。合法缩小目标要重新冻结并批准新revision。
- 新批准若与旧QUEUED/RUNNING、PROCESSING或UNKNOWN存在同商品同平台目标交集，则拒绝重发并要求核对。已经精确官方回读的旧目标可接受新的明确批准；新plan不能复用旧plan的draft proof。
- 本包不提供把未知外部结果“标成已核对”的写接口，也不替用户推断平台成功。未知结果的官方核对和后续授权流程仍需在后续受控包完成；它不是永远禁止其他商品/平台或合法新批准。

## 验证边界

新测试真实经过临时 ReleaseStore 批准/持久快照、CollectBox内部证明账本、loopback HTTP、runner、运行/报告账本、TikTok v4投影、独立 publisher 与 Miaoshou transport。只替换 provider post 和官方回读接口的合成边界，并将后台调度同步化以确定性读取结果。PUBLISHED测试中的官方回读是合成 fixture，不能当成真实平台发布证据；默认Unavailable回读保持PROCESSING。

覆盖并发HTTP及两个独立Python进程的SQLite原子登记、进程状态停在QUEUED/RUNNING、旧随机ID兼容、正常/单店/全店UNKNOWN重复、六店及部分缺证、类别故障恢复、跨商品/plan/digest/schema/target、官方读回身份和新批准子集。相关 Ozon多变体、Shopee区域、下架和写入预算测试保持。未触发真实平台/付费写、未替换运行服务、未推送Git。

早期新测试使用临时DB但曾让报告文件落在本隔离树默认报告目录；已修为DB和报告文件都显式位于tmp_path。70份早期合成报告路径/hash已列外部审计证据，它们是本新树的ignored测试产物，未修改旧工作树、未纳入提交。Windows长测试路径另触发报告临时文件MAX_PATH；最终验证使用更短的workspace自有临时目录，不改生产报告实现或系统配置。所有红例、环境问题和最终结果保存在外部回执中。

## 后续仍未整合

B4B的46个逐路径三方取舍、ToAPI残留与退役要求核对、真实平台核对/折扣执行，以及运行服务替换不属于本提交。现有折扣与下架工具没有被删除或改写；离线测试不证明其线上可用性。
