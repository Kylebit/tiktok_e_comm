# Orbit UI 回归测试交接说明

本文档给主 agent 和并行 agent 使用，说明当前已经落地的浏览器级回归测试框架、覆盖范围、执行方式，以及什么时候必须跑。

## 1. 这套测试解决什么问题

这是一套基于 Playwright 的 UI 冒烟 / 回归测试。

目标不是验证外部平台真实业务结果，而是尽快发现下面这类问题：

- 页面打不开
- 路由切错
- 关键 DOM 消失
- 主流程入口按钮丢失
- 重要容器被隐藏
- 商品目录 / 新品工作台 / Bot 面板页面壳子被改坏

它主要给 Orbit 当前三个前端入口兜底：

- Orbit OS
- Orbit Treasury
- Orbit Rus

## 2. 当前测试分层

当前项目已经形成三层最小回归入口：

### A. Python smoke

入口：

- `work/tiktok_e_comm/tools/frontend_smoke.py`

作用：

- 检查主要前端 / API 基础连通性
- 验证 Treasury 当前端口与基础预览接口

### B. Python unit / contract

当前统一由脚本串起来，已纳入：

- `work/tiktok_e_comm/tests/test_catalog_listings.py`
- `work/tiktok_e_comm/tests/test_miaoshou_client.py`
- `work/tiktok_e_comm/tests/test_new_product_workbench.py`

作用：

- 验证后端逻辑、数据拼装、接口封装没有被改坏

### C. Playwright browser smoke

目录：

- `work/tiktok_e_comm/tests/playwright/`

作用：

- 用真实浏览器打开页面
- 检查页面是否能渲染出关键 UI 壳子
- 检查目录页和 Treasury 页面最基本的交互容器还在

## 3. 当前 Playwright 覆盖了哪些点

### 3.1 页面打开类

- `smoke/os.spec.ts`
  - Orbit OS 能打开
- `smoke/treasury.spec.ts`
  - Orbit Treasury 能打开
- `smoke/rus.spec.ts`
  - Orbit Rus 能打开

### 3.2 商品目录类

- `catalog/catalog-load.spec.ts`
  - 商品目录主页面壳子存在
  - 关键区域能渲染

- `catalog/catalog-image.spec.ts`
  - 商品目录图片区 / fallback 壳子存在
  - 用来防止图片列整体消失

- `catalog/shopee-sync-ui.spec.ts`
  - TK -> Shopee 同步入口相关 UI 还在
  - 进度条容器壳子仍存在

### 3.3 新品工作台类

- `treasury/treasury-input.spec.ts`
  - Treasury 输入区存在
  - 第一波预览流程的基础容器还在

- `treasury/treasury-review-shell.spec.ts`
  - Treasury 审核阶段容器仍存在
  - 阶段提示、动作按钮、预览区未被删掉

### 3.4 桌面台 / 飞书 Bot 类

- `bot/bot-panel.spec.ts`
  - 桌面台内 Bot 面板模块壳子存在
  - 主内容区可渲染

## 4. 明确不测什么

这套 Playwright 现在还**不负责**下面这些事情：

- 不验证妙手真实发布是否成功
- 不验证 TikTok / Shopee / 飞书外部服务真实返回
- 不验证价格公式的业务正确性
- 不验证“图片内容好不好看”
- 不验证人工审核结论是否合理

也就是说，它是“页面和流程壳子回归测试”，不是“业务全链路验收测试”。

## 5. 统一执行方式

统一入口：

- `work/tiktok_e_comm/scripts/run_regression.py`
- `work/tiktok_e_comm/run_regression.cmd`

常用命令：

```bash
python scripts/run_regression.py --mode python
python scripts/run_regression.py --mode playwright
python scripts/run_regression.py --mode all
```

Windows 也可以直接执行：

```bash
run_regression.cmd --mode all
```

## 6. Playwright 依赖和运行方式

### 6.1 包管理

前端测试依赖定义在：

- `work/tiktok_e_comm/package.json`

当前脚本：

- `pw:install`
- `pw:test`
- `pw:test:smoke`

### 6.2 Node 运行时

为了避免系统 Node 安装失败，目前已经兼容项目内便携 Node：

- `work/tiktok_e_comm/tools/runtime/node-v22.23.1-win-x64/`

统一回归脚本会优先使用这个本地 Node。

### 6.3 Playwright 配置

配置文件：

- `work/tiktok_e_comm/tests/playwright/playwright.config.ts`

辅助目标解析：

- `work/tiktok_e_comm/tests/playwright/helpers/targets.ts`

## 7. 最近一次落地结果

当前状态：

- Python smoke：通过
- Python units：通过
- Playwright smoke：通过

当前 Playwright 通过数：

- 9 / 9

## 8. 什么情况下必须跑

### 必跑 Playwright 的改动

如果 agent 改了下面任何一类内容，提交前建议至少跑：

```bash
python scripts/run_regression.py --mode playwright
```

适用场景：

- 改了 Orbit OS 页面
- 改了 Orbit Treasury 页面
- 改了 Orbit Rus 页面
- 改了商品目录 HTML / JS / 样式
- 改了新品工作台 HTML / JS / 样式
- 改了桌面台 Bot 面板渲染逻辑
- 改了影响页面可见性的后端接口

### 只跑 Python 的改动

如果只是：

- 改纯后端逻辑
- 改定价公式
- 改数据清洗
- 改 Miaoshou / TikTok client 封装

优先跑：

```bash
python scripts/run_regression.py --mode python
```

### 主链路改动

如果改的是新品闭环、商品目录、跨模块状态同步，建议直接跑：

```bash
python scripts/run_regression.py --mode all
```

## 9. 推荐给多 agent 的执行规则

建议在多 agent 协作里固定成下面这条：

1. 改 UI 页面的 agent：必须跑 Playwright
2. 改后端逻辑的 agent：必须跑 Python
3. 改主链路或跨模块状态的 agent：跑 all
4. 没跑测试的 agent，不允许说“已完成”

## 10. 已知边界

这套测试目前还是“最小可用框架”，还没到完整验收级别。

已知边界：

- 主要校验页面壳子，不深测业务结果
- 对图片内容只检查容器 / fallback，不做视觉比对
- 没有覆盖真实妙手待发布结果校验
- 没有覆盖真实飞书消息收发闭环

## 11. 下一步建议

下一轮可以继续加三类测试：

### A. Treasury 主链路回归

- 1688 / ERP ID 输入
- 第一波预览完成
- 审核区渲染
- 二审按钮状态流转

### B. 商品目录稳定性回归

- 图片加载失败 fallback
- TK -> Shopee 按钮点击后的状态变化
- 列表增量刷新后新 SKU 是否出现

### C. 桌面台 / 飞书 Bot 回归

- Bot 启停状态 UI
- 实时日志区域
- 固定回复模板壳子

## 12. 给主 agent 的一句话摘要

当前 `work/tiktok_e_comm` 已经有一套可执行的最小回归框架：`run_regression.py` 统一串起 Python smoke、Python unit 和 Playwright browser smoke；Playwright 已覆盖 Orbit OS / Treasury / Rus、商品目录、TK->Shopee 入口、Treasury 审核壳子、桌面台 Bot 面板，共 9 条用例并已跑通。以后凡是改 UI 页面、页面路由、前端容器、主链路状态同步，都应该把这套测试作为交付前的基础门槛。
