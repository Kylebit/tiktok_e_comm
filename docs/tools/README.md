# 工具与 Skill 最短入口

机器目录是 `config/capability_catalog.json`；中文卡片由 `orbit_tools.py help 能力ID` 读取同一份数据。目录覆盖原 20 项逻辑 Skill 和 19 组工具，新增 TikHub 的可移植 Skill。vendor 副本、pytest 产物、plugin cache 别名不重复计数。状态描述当前源和本地可用阶段，不表示账号权限、余额、提供方健康或业务已完成。

## 新项目

本包最低 Python 为 3.11（S02-D 锁和 K00 清理错误使用 `BaseException.add_note`），实际验证环境为 Windows、Python 3.12.8、pytest 8.4.2。Lingshi preview 依赖 `requests`；DuoPlus/TikHub 与有限知识导出的运行依赖为 Python 标准库。知识导出还要求目标文件系统支持同目录原子 hard-link 发布。pytest 只用于回归，其他 Python 版本和文件系统未验证。

选择稳定、可恢复的 runtime 根 `R` 与项目根 `P`；不从工作目录猜测个人配置。复制 `config/tool_profile.example.json` 到项目的 `profile.json`，填写 tenant、相对产物路径、官方 origin 和环境键名。profile 不接受密钥、店铺、品牌、价格或旧用户身份。凭据由用户在目标进程单独设置，不随代码包迁移。

```text
python R/scripts/orbit_tools.py --runtime-root R catalog
python R/scripts/orbit_tools.py --runtime-root R help lingshi
python R/scripts/orbit_tools.py --runtime-root R --project-root P --profile profile.json doctor --capability lingshi
python R/scripts/orbit_tools.py --runtime-root R --project-root P --profile profile.json preview lingshi media --payload request.json
```

Lingshi 请求示例：`{"model":"example-model","prompt":"Use supplied facts only","params":{}}`。这是离线参数计划，不证明模型可用。TikHub 和 DuoPlus 的完整参数与恢复办法在各 Skill references。所有 help/doctor/preview 只做本地检查；doctor 仅检查环境键名存在性，不读取值。真实 SDK 或 execute 才可能调用提供方，现有同范围授权应直接复用。

## 包与临时安装

DuoPlus 实际设备读取使用 `read duoplus devices|info|status|apps|installed-apps --payload request.json`，同样要求显式 runtime/project/profile；它会读取所选进程凭据并联网，无需安装批准文件。`preview` 没有读取设备。DuoPlus/TikHub 的新传输均禁止自动 redirect 和环境/注册表代理发现，3xx 保留错误/UNKNOWN，不跟随到第二跳。

```text
python R/scripts/package_agent_tools.py --runtime-root R --destination NEW_PACKAGE
python R/scripts/package_agent_tools.py --runtime-root R --destination NEW_PACKAGE --build
python R/scripts/sync_product_publication_skills.py --registry --runtime-root R --destination-root INSTALL_ROOT --skill use-lingshi-ai --check
python R/scripts/sync_product_publication_skills.py --registry --runtime-root R --destination-root INSTALL_ROOT --skill use-lingshi-ai --install
```

`orbit-portable-tools/6` 包只包含固定代码/文档白名单、三个 portable Skills（use-lingshi-ai、control-duoplus-cloud-phone、research-tikhub-reference）、K00 有限知识入口、完整 runtime 关闭脚本的源码副本和 manifest；UTF-8 文本使用 LF digest，二进制保持原字节。知识入口见 `docs/knowledge/README.md`，仅含导出、固定版本加载/检索，不含 Vault、知识快照、shadow/runtime/store 或完整 product_agent。目标目录必须全新，失败保留可检查的部分产物并返回非 0。安装复用既有全文件 Skill manifest；先检查全部所选源/目标，extras 不删除、不覆盖。旧版本放在安装根 `.history/skill-id/digest`，恢复时以它为 source 走相同 parity 检查。未修改个人安装目录或旧 junction。

关闭条目 `publication-closure` 仍是 `WORKFLOW_RUNTIME_REQUIRED`：完整业务服务端及原始批准、发布证据不随 portable 包分发，包级目录 doctor 会报告缺少服务端模块。[close_product_publication.py](../../skills/publish-approved-product/scripts/close_product_publication.py) 是另一个边界：仅用标准库的 loopback 客户端，portable 副本可显示 `--help`，但不会自带或启动服务。必须显式提供 `--base-url` 与 `--expected-root`，客户端先经 `/api/health` 核对服务身份、根和依赖，再提供 `doctor`、`prepare --input`、`record --prepared`、`latest --offer-id --plan-id`。客户端 doctor 是运行时检查，不是业务授权；prepare 由服务端验证精确计划与目标，record 由服务端复验后写不可变本地回执，latest 用于同计划对账。`record` 传输或回包不明时不自动重试，保留返回的预期 closure ID/digest，先以同一 offer、plan 调用 `latest` 对账。portable 分发不授予关闭或发布权限，也不意味着缺失业务服务端时能独立关闭。

当前 /6 保留 /5 的祖先检查与 /4 的普通/扩展本地盘符解析结果比较：原词法检查后，从盘符根到所选 root 及目标逐级核对链接和 Windows reparse 属性。registry、Skill 同步与工具 launcher 先校验原始路径再 resolve；外部调用者已经解析过的路径无法恢复 alias 来源。深目录恢复边界只见[路径预检](#write-paths)。旧 /5、/4、/3、/2 包与回执保留，不覆盖已有 runtime。包中 R3 子目录只有可独立显示 help 和验证显式配置的关闭客户端副本，没有 SKILL.md、服务端关闭模块或关闭参考文档；完整 R3 保持 `installable=false`，不含政策、incident registry、审批、数据库或凭据。portable 验证不等于 APP 安装、部署或真实服务验收。

单项 Skill 同步脚本使用同包 `shared_platform/capability_runtime.py` 校验源和目标；请保留受信 launcher 的包布局，缺少该文件会明确失败，不从用户选择的待校验 root 加载代码。显式临时安装仍走原 `--check/--install`，个人安装不随包更新而自动迁移。

源码维护者组合已核准提交后运行 `python R/scripts/refresh_tool_manifests.py --runtime-root R --source-basis-commit EXACT_CURRENT_HEAD` 检查；审过源码差异后加 `--write`，由生成器更新声明文件的实际 hash、组合来源和 runtime manifest。参数必须是当前精确 HEAD；只读检查沿用已记录且经 Git 核实为当前祖先的生成 basis，并重算实际文件 hash。生成后正常提交不会要求再次生成；实际内容变化仍失败。显式 `--write` 才把生成 basis 更新为当前 HEAD。basis 是生成时的真实 Git 父检查点，最终内容身份由生成的逐文件 hash 与包 digest 固定；它不是业务批准。历史 `source` 保留字段只用于溯源，`composition_basis_commit/composed_file_digest` 绑定当前组合。不得修改 hash 来掩盖未审代码，或把源生成器当分发包的修复入口。

生成器的 `ok` 只说明源清单一致。明确依赖完整业务 runtime、待组合或待宿主接入的缺文件条目单列为 `unavailable_capabilities`；它们不可执行，也不代表健康。portable 条目、未知阶段的缺文件或白名单包本身缺文件会阻止生成并返回非 0，不能靠刷新清单隐藏缺失。具体能力仍须通过所选 doctor；账号和业务健康另需官方回读。

完整业务 Skill 可在完整 runtime 中通过相同 registry 安装；实际执行脚本仍从明确的完整 runtime 运行，不能把复制到个人 Skills 的脚本目录猜成业务根。`--check` 是安装一致性，doctor 是文件/依赖检查，两者都不等于业务授权或生产健康。原三项 publication 同步模式保留兼容。

## 当前组合边界

第一轮目录描述完整源码中的 R1 Skill（`skills/prepare-product-publication/SKILL.md`，不随 portable 包分发）的准备入口：一个精确 Offer ID 与完整目标店铺，产出事实、文案、价格和图片计划候选及缺项。该入口不承诺个人安装扩展中的 manual intake、自动批准回执或付费文案；第一轮零外部写入，妙手同步属于第三轮。持久记录中的旧 `DEFERRED_TO_SECOND_ROUND` 仅是兼容标签，按当前阶段合同解释。

R2 消费已审 S02 预算、checkpoint、返工与时间精度合同，通用 Lingshi CLI 不另造付费执行器。当前源码已组合 B4B 的 R3/COMMON、折扣、publication_autopilot、两个 product-family JSON，以及后继 Q01/S03 修正。它们属于完整业务 runtime，不随 portable 包分发；目录阶段为 `WORKFLOW_RUNTIME_REQUIRED`，不代表业务已经执行。

R3 使用完整 runtime 中的 `skills/publish-approved-product/scripts/prepare_publication_execution.py --offer-id OFFER --base-url VERIFIED_SERVICE` 读取阶段；COMMON 执行仍要求精确 plan/token 与确认参数。个人 R3 与候选的差异按当次完整文件 manifest 逐项核验，不能自动删除或补入来源未确认的额外文件；R3 保持不可经 registry 安装。最终候选的默认 compiler 还需要显式政策和 incident registry；缺文件时 doctor 明确报告，不能从个人安装中自动补取。临时 portable Skill parity 不证明个人 R3 parity。

DuoPlus 的 preview 不读取设备，read 才是外部查询；安装先保留持久记录，结果未知时恢复该记录并只读核对，不自动重装。installed-apps 只能证明包名存在，不能证明版本或某次未知安装已经成功。对应操作、输入与同范围授权复用见 [DuoPlus 合同](../../skills/control-duoplus-cloud-phone/references/api.md)。

折扣 canonical 已可通过 registry 显式安装，但当前默认 writer 仅覆盖 LivelyHive TikTok SEA 四店，需已批准冻结价格、发布回读和原 OneClick 账本。Shopee 与浏览器折扣执行仍有缺口；旧 batch executor 已退役。平台 adapter 的本地文件存在不等于账号或目标可用。

delist canonical 与 r4 已冻结完整来源一致；其 `all` 是 OrbitHive 既有租户范围，不能进入通用 profile。Seaya 和 profit 继续使用领域 Skill 原合同；Seaya 领域修正与资源保持。H3 使用独立插件及显式运行配置，宿主浏览器/文档/ImageGen/工具通过会话工具清单或工具搜索发现，不能复制整个 cache 当 SDK。妙手 vendor 仅作协议参考，正式发布走已审 adapter/runner。ToAPI 保持退役，旧回执不改 provider 身份。

## 知识工具的使用边界

`publication-knowledge` 已在 portable /6 白名单与完整源码的方法卡投影中。来源评审、两个 profile schema 的区别、preview/export/固定版本 inspect 及恢复只见[知识入口](../knowledge/README.md)。卡片是命令说明，复制不会执行；preview 不自动生成 snapshot，需要显式导出新文件后再填入检索命令。知识版本不是当前执行许可或新鲜库存/利润，完整业务运行器消费尚未接入。

## 给 UI 的消费合同

读取 `catalog` 的 `skills` / `tools` 数组：`id` 稳定，`aliases` 保留原目录名称，`stage` / `required_files` / `recovery` 说明能力边界，`file_digests` 与 `source` 用于追溯。`doctor` 返回每项缺文件、漂移和缺 Python 库；缺项不能显示 READY。目录读取不触发 provider、账户健康或预览执行。最终组合才能更新 R2/R3/折扣页面状态，UI 不维护第二份手写能力状态表。

<a id="write-paths"></a>
## /6 安装和打包路径预检

/6 替代 /5 用于后续分发，旧包保留、不覆盖既有安装。新增的是可恢复性改善，不是全面 Windows 长路径支持。单 Skill 的 --check/--install、整组/registry 检查与安装、包 preview/build 共用只读路径预检；registry 在写第一项之前检查所有所选目标和完整64位digest历史树。

Windows 工具支持预算按UTF16单位分别计算：目录最多247、文件最多259、每个组件最多255，包含相同目录原子临时文件的32位hex命名模板。这是本工具的保守支持范围；实际证据来自64位Python3.12.8、LongPathsEnabled=0的当前主机（目录247成功/248 Win206，文件259成功/260失败），不是所有Windows的统一结论。未启用扩展namespace，也未修改系统配置。

LONG_PATH_UNSUPPORTED 给出阻断路径、目录/文件/临时模板类型、测量值和建议的目标根长度预算。agent 可在用户已授权的临时输出范围内自主选择更浅的 destination-root，报告实际路径并重跑同一 check/preview；这不增加批准轮次。用户明确指定的安装目标不能静默换目录，须保留原目录和成果，给出具体替代路径供其决定。工具本身不会自动换位置或截短内容 hash。临时模板中的32个0仅表示尚未分配的同长度UUID，不表示已存在文件。

PARTIAL_BACKUP_REQUIRES_REVIEW 表示已有history目录未通过完整manifest核验；先保留并核对当前安装、预期digest与备份，不能把目录存在当成功。完整备份可经既有单Skill --source VERIFIED_BACKUP --destination EXPLICIT_TARGET --check/--install 路径恢复；部分备份不能直接当完整恢复源。意外I/O中断返回已完成项、可能的临时/备份路径和恢复指引，保留部分结果，不自行清理或重试。这些预检不承诺对所有存储故障事务回滚。
