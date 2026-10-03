# 灵识 API 已核对契约

核对日期：2026-08-20。来源为用户已登录的 `qb.lk666.ai` 开放 API 页面、API 文档和用户下载的第三方 Skill。网页资料可能变化，执行付费调用前应重新查询只读能力清单。

## 基础地址与认证

- 模型兼容 API 基础地址：`https://api.lk888.ai`
- Bearer：`Authorization: Bearer <key>`
- Anthropic 兼容端点：`POST /v1/messages`，也接受 `x-api-key`
- OpenAI 聊天端点：`POST /v1/chat/completions`

## 只读能力发现

- `GET /api/v1/skills`
- `GET /api/v1/skills/guide`
- `GET /api/v1/skills/balance`

账号未充值时，只读能力端点也可能返回 `403 recharge_required`。

## 已核对图片端点

文档明确列出以下 OpenAI 图片兼容调用：

- 模型：`gpt-image-2`、`gpt-image-2-guan`
- 生成：`POST /v1/images/generations`
- 编辑：`POST /v1/images/edits`，multipart 上传 `image`

页面还展示 `nanobanana-pro`、`nanobanana-2`、`qwen-image-max` 等模型，但本次没有核实其完整参数与返回结构，因此客户端不得猜测调用。

2026-08-20 使用新建 Key 只读查询后，平台能力版本为 `2026-08-16`，当前推荐的动态媒体契约是：

- 模型目录：`GET /api/v1/skills/models?type=image`
- 模型参数：`GET /api/v1/skills/models/{name}`
- 当前价格：`GET /api/v1/skills/models/{name}/pricing?status=active`
- 提交任务：`POST /api/v1/media/generate`
- 查询任务：`GET /api/v1/skills/task-status?task_id={id}`
- 付费提交成功必须按响应中的 `code == 200` 判断，并读取 `data.task_id`
- 任务查询在 `is_final == true` 后停止；未知结果时先查用量或任务，禁止盲目重提

当前动态目录中的别名对应关系包括：

- Nano Banana Pro：`gemini-3-pro-image-preview`
- Nano Banana 2：`gemini-3.1-flash-image-preview`
- 千问 image-max：`qwen-image`

动态目录和模型详情优先于网页营销示例中的旧别名。

## OrbitHive 门禁

- 只读能力发现默认允许。
- 聊天、生成、编辑均视为付费外部调用，必须显式 `allow_paid_request=True`。
- 本地图片上传必须再显式 `allow_external_upload=True`。
- 当前商品发布工作流不会因为安装此 Skill 自动切换提供方。
