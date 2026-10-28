# 2026-09-28 正式 UI 切换：可复用的本地门禁

这份记录只总结 `1690dda0` 替换 `2264fefa` 的本地网页服务过程。`LIVE_COMMIT_IDENTITY_ONLY` 与非作者 live readback 已成立；业务 worker 和 provider 执行没有因此开放。证据原件在 `D:\OrbitEvidence\convergence-20260919\production-switch-1690dda0-20260928\formal-cutover-1690dda0-20260928-r2\` 及相邻的 `production-switch-1690dda0-independent-20260928\`，不要复制里面的端口、PID、路径和 SHA 作为下次部署输入。

| 事项 | 这次实际发生了什么 | 下次最小规则 |
| --- | --- | --- |
| Work Order 封存后变更 | 首版封存绑定旧 SHA `04f64d…`，独立复审作废该门禁并重做 r2；正式服务未因此误切换。见 `FORMAL_GATE_INDEPENDENT_REVIEW.md`。 | 独立复审固定封存文件 SHA，停旧前即时逐文件重核；任何变更重新封存和复审。`scripts/verify_sealed_deployment_inputs.py` 只给出只读 PASS/HOLD，不授予切换权。正式部署后的 Work Order 更新会让旧封存自然失效，不应据此声称历史切换失败。 |
| `python -O` 跳过 `assert` | 6f55 版脚本被静态复审 HOLD；没有以优化模式执行正式阶段。4a578 版移除安全 `assert`，入口拒绝优化模式。见 `CUTOVER_SCRIPT_REVIEW_6F55_HOLD.md` 和 `CUTOVER_SCRIPT_INDEPENDENT_REVIEW_4A578_GO.md`。 | 执行门禁使用显式 `if ...: raise`；入口拒绝 `-O`/`PYTHONOPTIMIZE`，测试覆盖优化模式。 |
| Task 注册可能自启 | 6f55 版候选 XML 含 LogonTrigger/StartWhenAvailable，注册后才写回执，属于复审发现的风险；没有证据表明实际自启。最终先注册 disabled Task 并写 `REGISTER_REQUEST`，再受控启用/启动。 | 先封存请求及旧定义，注册禁用的候选，读回 Task/PID/端口语义一致后才启动。 |
| 启动后缺 `STARTED` 回执 | 早期恢复分支只认 `START_REQUEST`/`STARTED` 的部分组合，复审发现异常中间态可能无法恢复；正式未触发。最终脚本在无 `STARTED` 时仍以精确进程、Task、DB 身份决定保全或恢复。 | 每一步写请求前回执；恢复按现场事实识别中间态，不把“缺成功回执”当成“没有启动”。有新本地写入先保全，不能覆盖旧快照。 |
| health `state`/`status` | 真实 `/api/health` 使用 `state: READY`。早期脚本把字段当 `status` 是预运行检查发现的错误；最终脚本改为 `state`，正式回读通过。 | 按当前服务合同检查 `commit`、`state`、`identity_only` 与 runtime 的 `web-only`/worker=false；测试用真实结构的响应，不靠宽松 truthy 判定。 |
| 知识页浏览器首探 | 独立正式浏览器在 `domcontentloaded` 后立刻读 body 得到 `body too short`；有界重跑通过，无页面错误或写请求。见 `live-browser-1690/RESULT.json` 与 `live-browser-1690-r2/RESULT.json`。 | 等待知识页实际异步内容的稳定可见标记，再断言内容。保存首次失败和复跑结果；不要把时序误报归类为页面故障。 |
| 私有停服脚本语法 | 首次私有停服脚本 SyntaxError 发生在正文执行前，首次 stderr 未独立归档；修正后私有 PID 退出。见 `FORMAL_GATE_INDEPENDENT_REVIEW.md`。 | 在任何切换脚本交付前做语法检查；失败原始 stderr 随证据封存。 |

## 下一次部署的最小顺序

1. 核 Work Order、Git HEAD/tree、候选部署文件和独立复审回执；对封存 JSON 及其中每个文件做 SHA 校验。示例：`python scripts/verify_sealed_deployment_inputs.py <INPUT_HASHES.json> --expected-manifest-sha256 <独立复审固定的 SHA>`。只读 `PASS_READONLY_INPUTS_MATCH` 不是 GO；缺独立复审的期望摘要时保持 HOLD。
2. 在静止窗口即时重核唯一旧 PID/create time、Task 原始定义、监听端口及客户端、数据库 file ID/sidecar/物理与逻辑 SHA，并做本时点 SQLite 在线一致快照。旧窗口快照不能代替新核查。
3. 停旧前保留 Task/配置恢复件；精确停旧，等 PID 退出和端口释放；注册 disabled 候选并回读，再明确启用/启动。把 `STOP_OLD`、`REGISTER_REQUEST`、`REGISTERED`、`START_REQUEST`、`STARTED` 分开留证。
4. 任一阶段不确定时先查 PID/Task/端口/DB，尤其不要因缺 `STARTED` 就盲目重启。只有确认无新本地写入才可恢复旧服务；快照不得覆盖有新写入的正式库。
5. 新进程的 health/runtime、GET、真实浏览器和稳定账本核对通过后，仍需非作者 live readback 才能称正式部署完成。浏览器等待实际异步就绪，不以固定延时掩盖失败。页面成功不代表业务任务或外部平台完成。

本仓库的校验器不停止服务、不注册 Task、不连数据库、不发送 HTTP，也不接收部署凭据。正式执行脚本应在同一进程内、紧邻每个不可逆阶段重复关键事实检查；外部只读校验结果可能在下一瞬间过期。
