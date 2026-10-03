# R3 项目配置的最小接续

先用选定工程源码读取明确的配置根；此入口仅输出脱敏 JSON，不访问网络、数据库、
服务，不复制文件，也不创建政策、审批或 registry：

```text
python -B scripts/publication_config_audit.py --config-root <绝对配置根>
```

可通过 `--policy-path` 和 `--incident-registry-path` 指定该根下的相对路径。
此离线入口不继承环境中的 `ORBIT_R3_*`。文件不存在返回 `MISSING`，路径逃逸或链接
返回 `UNSAFE_PATH`；状态沿用运行时既有 validator。退出码 0 仅表示两份文档被现有
validator 接受，退出码 2 表示配置诊断存在阻塞。两种结果均不授予执行权。

最小修复无需改动加载器。先在明确的私有配置根保全并审核来源文件，再由服务所有者
配置已有 `ORBIT_R3_CONFIG_ROOT`、`ORBIT_R3_POLICY_PATH`、
`ORBIT_R3_INCIDENT_REGISTRY_PATH` 启动参数，按运行时治理安排接续。离线检查不会
修改正在运行的服务。个人安装目录和旧正式工程仅可作为被审计的来源，不能自动回填。

政策应保留真实授权归属、时间和精确范围；当前会话已有授权不因模板留空而撤销，
也不能把旧政策的付费上限或重试预算直接移植为本次授权。历史账本与当前 provider
对账分开。示例政策的 DRAFT、空授权、未定预算有意阻止执行，填写时逐项绑定真实依据。

registry 应保全全部已确认、待合并和延期行。检查每个修复提交的存在性、当前源码
祖先关系及其回归测试；`CONFIRMED` 字样及 schema 通过不能证明这些事实。
不要用空 incidents 列表补齐缺件，也不要把存在同名测试文件当作修复验收。
示例 registry 的 null 有意使 validator 拒绝，避免空历史被误认为已配置。

```text
python -B scripts/publication_config_audit.py --config-root <工程绝对根> --policy-path config/examples/product_publication_autopilot_policy.example.json --incident-registry-path config/examples/publication_incident_registry.example.json
```

示例检查应返回 `AUTHORITY_UNCONFIRMED` 与 `INVALID`。示例文件均不是运行时默认路径。
即使真实文件返回 `CONFIGURED`，仍须分别满足当前商品的 R2 采用与 QA、COMMON
准确读回、完整最终候选及最终平台发布批准；配置检查不能替代这些合同。
