# 自动上品发布 V2：需求追踪与现状差距矩阵

状态：`DRAFT_FOR_KYLE_REVIEW`

## 1. 追踪规则

- 每个实现任务至少引用一个 `PRD-*`。
- 每个需求至少对应一个自动化测试。
- `CURRENT` 只描述代码，不构成需求依据。
- `GAP` 在 Kyle 批准本规范后才能转为实现任务。
- 修改旧测试的前提是证明旧测试与已批准需求冲突；禁止为了让新实现通过而删除门禁。

## 2. 需求—实现—测试矩阵

| 需求 | 目标行为 | 当前实现证据 | 当前结论 | 必须新增/保留的测试 |
| --- | --- | --- | --- | --- |
| PRD-001 | 每次点击创建新尝试 | `OneClickReleaseStore.start_explicit_batch()` 增加 `batch_sequence`，但重置同一 job 的目标行 | `PARTIAL/GAP` | `RED-HISTORY-001`；验证独立 attempt 行 |
| PRD-002 | 历史不参与准入 | 后端允许终态后显式 batch；UI 仍把上次状态放主区，旧 recovery 逻辑仍存在 | `PARTIAL/GAP` | 历史矩阵 + 按钮可用性 |
| PRD-003 | 三个平台隔离 | 三个 HTTP 路由和 scope 已分开；UI 仍共享 `releaseSubmitting/oneClickExecution.posting`，后端仍共享一个 job/全局锁 | `PARTIAL/GAP` | `RED-ISOLATION-001/002` |
| PRD-004 | 三平台走妙手 | 当前 adapter registry 与妙手 direct-store 路径存在 | `CURRENT` | 每平台 handler→worker→transport 纵向测试 |
| PRD-005 | 避免重复确认 | 新平台按钮无需旧 publish-all checkbox；页面仍保留大量 legacy 控制代码 | `PARTIAL` | 浏览器只见目标控件；旧控件不可聚焦 |
| PRD-010 | 只共享批准计划事实 | `_oneclick_approved_context()` 统一校验计划 | `CURRENT/KEEP` | 三平台 stale plan 均 0 写 |
| PRD-011 | TikTok 使用当前草稿身份，GB 跳过业务差异校验 | 最新 bridge 已持久化逐目标 detail/shop proof；GB waiver 已存在 | `CURRENT/VERIFY` | 六站点 proof + GB 不因差异被排除 |
| PRD-012 | Shopee 只发全球商品且不依赖 TikTok | `/publish-shopee-global` scope 独立 | `CURRENT/VERIFY` | TikTok 任意状态下 Shopee 可启动 |
| PRD-013 | Ozon 独立 | `/publish-ozon` scope 独立 | `CURRENT/VERIFY` | TikTok/Shopee 任意状态下 Ozon 可启动 |
| PRD-020 | 如实区分本地接受、妙手接受、失败、未知 | 当前 `SUBMITTED_UNVERIFIED` 文案基本正确；分组仍混入上次状态 | `PARTIAL` | 文案与状态表精确测试 |
| PRD-021 | 单目标失败继续 | 现有 worker 有继续执行测试 | `CURRENT/KEEP` | 首/中/尾失败参数化 |
| PRD-030 | 再次点击是新 attempt，不是自动重试 | 当前是重置同 job 可变投影 | `GAP` | attempt_number 和不可变历史测试 |
| PRD-031 | 同平台防双击，不跨平台锁；重复点击只提示、不跳转 | 当前前后端存在全局 posting/lock | `GAP` | 同平台双击仅一 attempt + 可见提示 + 无聚焦/跳转 + 跨平台并发 |
| PRD-040 | 三个平台独立按钮 | 当前页面已有三个按钮 | `CURRENT/KEEP` | computed visibility/click 请求路径 |
| PRD-041 | 禁用必须有提示 | 部分控件有 `data-disabled-reason`；平台按钮存在 silent return 路径 | `PARTIAL/GAP` | 所有不可用原因可见；可用点击有反馈 |
| PRD-042 | 当前与历史分离；主页面不提示或计数历史 | 当前 `renderOneClickExecution()` 用“上次...”分组渲染主区 | `GAP` | 主页面无历史条数/横幅；历史变化不影响主区与 disabled |
| PRD-043 | 刷新/重启恢复 | durable job 和轮询存在；终态/历史边界混合 | `PARTIAL` | 重启恢复 + 终态后开放 |
| PRD-044 | 每条按钮/状态路径有真实点击与截图证据 | 现有 Chromium fixture 覆盖部分页面，但未为每个 phase 生成路径证据包 | `GAP` | `VISUAL_TEST_PLAN.md` 全矩阵；双视口截图与 `path.json` |

## 3. 当前代码位置

| 位置 | 当前职责 | V2 处理意见 |
| --- | --- | --- |
| `modules/products/server.py::_start_collectbox_action` | 采集箱新批次/重新导入 | 保留，明确输出当前 input proof |
| `modules/products/server.py::_start_oneclick_release` | 三平台批次创建公共入口 | 拆成共享校验 + 平台 attempt 创建，不读历史 gate |
| `modules/products/server.py::_start_tiktok_release` | TikTok scope | 保留路由，改为 V2 attempt |
| `modules/products/server.py::_start_shopee_global_release` | Shopee 全球商品 scope | 保留路由，改为 V2 attempt |
| `modules/products/server.py::_start_ozon_release` | Ozon scope | 保留路由，改为 V2 attempt |
| `shared_platform/oneclick_release_controlplane.py::start_explicit_batch` | 在同一 job 内重置当前投影 | 兼容保留；V2 不以其作为历史模型 |
| `shared_platform/collectbox_action.py::internal_tiktok_publish_contexts` | 服务端内部逐目标 proof | 保留；作为 TikTok 当前输入 |
| `domains/channel_operations/oneclick_release_adapters.py` | typed prepare/dispatch | 保留平台 adapter 边界 |
| `modules/miaoshou/oneclick_release.py` | 妙手 prepare/dispatch | 保留已验证 payload 和 transport；不把状态治理下沉 |
| `web/static/product_workspace.js::renderOneClickExecution` | 混合显示当前和上次结果 | 拆为 current + history |
| `web/static/product_workspace.js::updateReleasePrimaryAction` | 三平台按钮 | 改为每平台 busy/input 状态 |
| `web/static/product_workspace.js::publishPlatformBatch` | 公共 POST helper | 参数化平台状态对象，禁止全局 posting |

## 4. 已识别的需求漂移

以下行为曾进入文档、测试或实现，但不是当前 V2 需求：

| 历史行为 | 来源类型 | V2 决定 |
| --- | --- | --- |
| 上一次 reconciliation 必须先处理才能重发 | 保守治理规则 | 删除为准入条件；保留历史事实 |
| COMMON 成功是所有平台发布依赖 | 旧架构 | 删除跨平台依赖 |
| 全部平台共用一个“一键发布已选店铺” | 旧 UI | 改为三个按钮 |
| 只有官方回读成功才算流程完成 | 旧渠道设计 | 当前版本不做官方回读 |
| 成功目标不能再次提交 | 旧幂等规则 | 允许新 attempt |
| 结果未知必须禁止重发 | 旧安全规则 | 允许 Kyle 显式新 attempt |
| TikTok 状态阻断 Shopee/Ozon | 共享 job/UI busy 副作用 | 禁止 |
| 历史状态卡片作为“唯一下一步” | 统一状态机设计 | 历史与当前操作分离 |

## 5. 永久回归套件

建议新增统一 marker 或清单 `release_v2_regression`，每次发布相关改动至少执行：

1. `tests/test_tiktok_collectbox_publish_bridge.py`
2. `tests/test_oneclick_release_controlplane.py` 中 V2 attempt/隔离子集
3. `tests/test_oneclick_release_http.py` 中三路由子集
4. `tests/test_release_ux_contract.py`
5. `tests/browser/release_ux_contract.js`
6. V2 新增的历史不阻断、平台隔离、目标继续执行、真实文案测试
7. `VISUAL_TEST_PLAN.md` 中 BTN/状态全矩阵，并保留每条路径的真实截图证据包

不能只跑 adapter 单元测试后宣称页面可用；不能只跑浏览器 fake fixture 后宣称真实 server contract 可用。纵向测试必须覆盖：

```text
真实按钮
 -> HTTP handler
 -> 持久 attempt
 -> worker claim
 -> production adapter registry
 -> fake-only Miaoshou transport
 -> 持久结果
 -> status API
 -> 页面渲染
```

浏览器门禁还必须输出：

```text
真实按钮 click
 -> 点击后即时反馈截图
 -> HTTP 接受/拒绝截图
 -> 每个不同 phase 截图
 -> 最终反馈截图
 -> path.json（请求摘要、状态序列、最终文案、console/pageerror）
```

## 6. 变更单模板

```text
Change ID:
关联需求 ID:
用户观察到的问题:
当前实现证据:
最小复现步骤:
修改前失败测试及输出:
根因:
明确修改范围:
明确不修改范围:
数据/状态迁移:
外部写入授权:
验收测试:
浏览器验收:
回滚方式:
Kyle 确认:
```

## 7. 评审结论记录区

本草案尚未批准。Kyle 评审后应在此记录：

- 文档批准版本；
- 批准日期；
- 接受的 `PROPOSED` 项；
- 被修改或删除的需求；
- 第一项获准实施的 Step；
- 明确禁止实现的扩展。
