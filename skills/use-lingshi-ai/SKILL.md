---
name: use-lingshi-ai
description: Safely inspect and use the Lingshi AI aggregation API for current model discovery, OpenAI-compatible chat, and verified image generation or editing. Use when the user asks to connect, inspect, test, or route model work through api.lk888.ai.
---

# 灵识 AI API

使用显式稳定 runtime 中的 `modules.sourcing.lingshi_client.LingshiClient`。独立项目的最短入口见 [portable-entry.md](references/portable-entry.md)；模型协议与历史核验日期见 [provider-contract.md](references/provider-contract.md)。安装 Skill 本身不提供运行库或账号权限。

## 工作顺序

1. 选择非秘密 profile 与稳定 runtime root。`doctor` 只检查所选环境键是否存在，不读取密钥、注册表、个人 C/D 配置或余额。
2. `preview` 离线调用当前 Client 的真实参数构建方法。需要当前模型/价格时再显式执行相应只读 SDK 方法，记录日期与来源；不要把提供方查询放入 doctor 或页面初载。
3. 聊天、生图和图片编辑先生成 preview，向用户展示模型、端点、预计用途和是否上传图片。
4. 付费请求必须绑定模型、用途、完整输入、额度及有效的既有用户授权。匹配同一冻结范围的持久批准直接复用；执行参数不是第二次业务批准。
5. 上传还须有对应材料/用途授权。OrbitHive 图片执行走现有 R2 `PaidRequestLedger`、checkpoint 与 rework 合同，不能用通用 Client 的布尔开关代替累计预算。
6. SUBMITTING/UNKNOWN 保留预算并恢复已有 task，不重发。已完成付费但 QA 拒绝的合法返工复用原 rework seam，保留旧费用。通用可移植 CLI 只提供 Lingshi 预览；不伪装为已接入通用付费执行器。

## 密钥与外部写入

- 真实密钥只能放在被 Git 忽略的 `config/lingshi.local.json` 或进程环境中。
- 不得把真实密钥写入 Skill、代码、前端、报告、日志或 Git。
- 不安装额外 HTTP MCP；项目现有 Python HTTP 客户端已经足够。
- 不自动向平台提交反馈、Bug、文件或个人信息。
- 安装本 Skill 不会自动修改其他工作流；提供方路由变更必须有明确的工作流配置和审计记录。
