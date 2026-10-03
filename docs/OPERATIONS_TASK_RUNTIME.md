# Orbit 任务与运行方式

## 2026-10-03 候选原生服务合同（SOURCE_ONLY，尚非正式安装事实）

本段说明新的隔离候选源码。下文涉及 49289/web-only、七条旧审核 POST、workspace-write agent、全局 worker 未接的描述保留为 **截至 2026-09-22 的历史合同与审计**，不能作为当前候选的操作要求，也不能据此开启全局业务执行。

旧通用 worker、历史 dispatcher 和旧任务批量接管仍关闭。候选正式启动器 `operations_launch.serve` 只有可信部署配置显式选择 `native_service_scope=explicit-new-task-and-decision/v1` 才注册原生服务；缺配置仍为原 web-only。固定 agent 可执行文件来自部署配置及精确文件身份，不能借 ambient 环境。ready 在注册成功后报告独立 scope 与能力，不把 `worker_enabled=False` 改成全局业务开放。

显式 `/api/orbit/tasks` POST 真正新建的 publication 使用创建事务原 `explicit_new_post_preparation` 事件，profit 使用独立 `explicit_new_post_profit_readonly` 事件。两者分别固定原 task ID、请求范围、来源键和当前 release，不复用 publication scope 表示利润。幂等返回已有任务不补事件；同版本重启只核验对应事件与当前原任务，不扫描首页历史队列，不把旧版本或同月复用旧任务自动收养。started/UNKNOWN 先按原回执对账，不重新启动 child 或付费请求。新利润执行仅原只读统计与本地产物，沿固定配置、真实租约、原请求范围和固定 monthly 生产器校验；缺结算、历史成本或广告资料仍显示原具体缺项。

首轮落盘由父服务调用原 R1 生产器，固定源码、任务数据、发布库父目录和 Offer 产物目录来自实际服务绑定，可位于不同本机盘；不接受网页自报目录或网络根。父服务持有 Windows 目录句柄，拒绝 reparse/多链接输出，并用独占新建临时文件的原写入器落盘、回读和冻结。该边界不是给 child 开 workspace-write：child 的只读观察结果仍不是完整准备回执。恢复复用原 task-owner/intent/prepared-reference/技术 decision/冻结回读校验，缺可信材料保持具体技术待办，不增加 COMMON 人工审核。

候选原生唯一终审通过后，服务只自动接续这次明确提交且已验证的决定，执行前重核原冻结业务内容与目标；技术重绑不新增人审，业务关键内容或目标改变才重新展示。相同决定不重复 provider 写，UNKNOWN 保留原 attempt，只读对账。此行为是候选源码接口，实际启用、任务续跑和每目标结果仍须由安装及运行回执证明。

候选已接入 typed category/事实 child→父验 schema→固定 sidecars→原 R1 生产器，以及原 R2 图片／QA／本地化生产器。child 仍只读，观察不是准备回执；完整私有输出、原 session/attempt 和来源可保留对账，已有 UNKNOWN 不二派。原阶段完成证据齐全且下一确切 business 从未尝试时才允许首次接续。R2 必须绑定 service-owned 原 paid-history 根、同产品历史 usage／原 checkpoint／task／输出，缺绑定在 baseline/reserve 前拒绝；不能用新空 root 清零预算，旧 usage 格式兼容也不制造缺失的 business/attempt ownership。

技术 COMMON 仍先于唯一市场终审：原完整冻结 scope 可含确切 `miaoshou:COMMON` 与七市场，COMMON 单独技术绑定，市场候选按原规范目标顺序派生，不改原冻结文件或摘要。GET/readiness 不写计划；显式同任务、当前租约和受信来源准备才惰性持久 exact PENDING 计划，经原 cap／UNKNOWN／官方逐字段回读后准备完整终审候选。只有一次市场决定；每目标执行／回读继续核原冻结内容与同决定，接受或线程退出不算成功。

上述为隔离候选源码合同，正式部署仍须当前具名 source/manifest、原 settings/任务账本/发布库/报告根、固定 executable、显式 schema 安装与回滚证明。独立 publication25、profit15、legacy21 的结果不能拼成统一验收；统一653曾因报告器容量在516个选中节点处中断，137未到，后继完整653/70仍需实际自然终态。正式安装、模型／价格／凭据可用性和外部商品结果未由此文证明。独立 exact 新 POST delisting 源码与 wire 守卫另包未运行，不据它声称本统一服务已接通第三模板，也不启旧任务或 token refresh。

首页统一组织运营任务，商品目录、商品上架、供应链、利润和知识工具仍是五个业务区域。用户在本轮明确授权了这个组织入口。

每个任务固定模板、对象与店铺范围、代码版本及 Skill/脚本指纹。上架审核仍使用原商品审核页；通用任务按钮不能制造业务批准。资料待办和平台处理中分别显示，平台处理中不进入“待我处理”。同一原审核回执验证通过后由本地执行器唤醒下一步，重启从持久回执接续。

任务库与商品数据库分开：`ORBIT_OPERATIONS_DATA_ROOT` 存储 `tasks.db` 和独立任务产物；商品数据库、凭据和历史报告使用部署时原有明确绑定，不能回落到新工作树空数据库。预览使用独立端口和数据目录，`ORBIT_OPERATIONS_ENV=preview` 禁止平台下架。预览不能覆盖稳定目录；所有路径先解析符号链接。

`ORBIT_OPERATIONS_AGENT_EXECUTABLE` 只接受部署者配置的 Codex CLI 文件路径。任务标题与资料通过 stdin 作为任务数据，不能拼接 shell。首轮事实、图片准备使用受控 workspace-write，仅写该商品产物及原流程状态；首轮骨架不进入正式审核，完整候选仍需用户在原页面确认。月报 agent 仅在独立输入目录准备真实财务输入，不修改源码、旧报告、凭据，不自动刷新。没有执行器时显示未连接；投递、准备摘要、HTTP 200 均不算业务完成。进程失联或超时保留执行意图和会话标识，不自动再发起，未验证子进程停止时明确保持未知。

上架、下架冲突以当前项目内部 SKU 与规范 target_label 为键，例如 `0001` / `shopee:MY`。当前接入的三平台发布后台和下架 Skill 使用同一持久操作账本，只有正式回读才释放未知外部动作锁。稳定部署在 `%LOCALAPPDATA%/OrbitHive/operations-runtime.json` 登记 `orbit-operations-profile/v1`、`environment=stable`、绝对 `data_root` 和 `release_identity`，使当前 Skill CLI 与 HTTP 服务共用账本。未升级的外部旧脚本、旧工作树不会自动得到这一保护；运营应从任务入口或当前部署 Skill 发起。

Agent 可用 `scripts/orbit_task.py --data-root <目录> create/status/attach` 登记工作和准确观测。`attach` 只投影外部任务，永不重复启动，观测必须带时间和证据。八月利润等既有任务应附着原执行者而不是另建计算进程。

共享操作 profile 的 `source_root` 必须明确绑定已登记的调用工程目录；跨工程安装的 Skill 若确需共用该账本，由部署者在同一 profile 的 `authorized_source_roots` 数组显式登记其实际调用 root。目录必须存在，按解析符号链接/junction 后的绝对路径精确比较（Windows 规范大小写），不接受父目录前缀、相对路径或通配符。显式指定 `ORBIT_OPERATIONS_PROFILE` 仍执行该身份检查；旧 profile 缺少 `source_root` 时需明确迁移，不自动授权当前工作树。未绑定调用者在打开账本前报错，不悄然取消冲突保护继续执行。

部署显式绑定的 `ORBIT_OPERATIONS_DATA_ROOT` 仍具有指定账本的职责。离线测试必须在导入运行模块前把该值及所有 profile/home 回退路径隔离到本包临时目录，并拦截超出允许根的数据库访问；仅 mock provider、设置 `tmp_path` 报告或替换 `release.db` 不等于隔离 `tasks.db`。不要把真实稳定数据目录继承给测试子进程。

月报由固定 Python monthly 生成器实际计算，agent 只能提交 `profit-producer-input/v1` 输入清单。生成器输出位于输入工作目录以外，回执固定输入、脚本、政策、输出 SHA 与参数；月份、平台、具体店铺、下单日期口径、完整结算证据与金额复核全部匹配后才进入结果阶段。不以周报替代月报，不继承历史临时广告比例。默认本地履约费来自当前 `report-policy.json`，当前为每本地父订单 4 元；跨境及未知履约仍由原域规则处理。缺历史成本或广告不能当真实利润完成，尚无连续结算日期显示等待结算资料。完成任务不意味着用户已经批准报告归档或知识库写入。

利润店铺解析使用 `ORBIT_OPERATIONS_CONFIG_ROOT` 指定的原配置工程根，部署必须明确指定；读取数据库和完整店铺登记，不因为未登录而悄然漏店。首次执行固定解析后的范围，补资料不重新扩店。预览不接真实财务凭据时会显示配置缺口。

已注册的历史图片候选沿用原注册身份和原图片审核；已存在图片不重复生图。新采集箱的目标流程是首轮事实与图片准备后，在可信累计写预算及 standing policy 约束下技术写入妙手 COMMON，并以任务绑定的官方逐字段回读确认；随后将完整冻结的全部平台候选送到原商品审核页，仅进行一次人工终审。预算、技术授权、逐字段回读或候选/目标矩阵不完整时保持 `BLOCKED`，不能显示可执行批准。历史 COMMON approve 后端入口和旧批准不因此获得自动执行权；已批准的精确冻结计划仅按原回执恢复，执行前有实质漂移才重新审核。平台发布结果仍需逐目标官方回读。

升级时先在预览验收并保留原稳定进程与数据；切换前核验代码/Skill 指纹、原业务数据绑定和正在执行的任务。版本变化不会把旧任务自动改为新版；旧任务保留原版本待原执行器完成或经明确迁移后恢复。

worker 首次启用时按精确 release 将任务准入时间持久写入 `tasks.db`；同版本重启沿用该时间，之后创建的任务可接续，首次启用前已排队或等待域回执的任务不会因连接执行器而批量续跑。web-only 浏览和无效恢复 ID 不创建准入记录。仅由部署者在 `get_runtime(..., worker_enabled=True, resume_task_ids=(<精确任务 ID>, ...))` 明确列入原任务后，才允许观察或领取该旧任务；列入前须核对版本、原批准、外部动作/UNKNOWN 和逐目标回执。该参数是代码装配合同，不是网页按钮或普通用户输入。当前固定 49289 维护部署仍为 web-only，尚无执行器启用回执。

部署者可用 `scripts/worker_admission_preflight.py --deployment <绝对部署 JSON> --agent-executable <绝对 CLI 文件>` 只读检查封存代码、manifest、业务数据绑定和任务库准入状态。输出 JSON 中的 `ok` 只表示这些静态检查没有阻断项，不代表 worker 已启动、三个模板已通过端到端验收或任何业务任务已完成。`web-only` 部署、未解决的域操作都会明确阻断；预检用 SQLite 只读连接，不创建准入表、不接管旧任务。专用 worker 的可执行启动路径仍须单独开发和验收。

`scripts/operations_worker_entry.py` 是独立的 worker-only **启动计划**入口：核对部署文件的代码根必须就是入口自身所在的封存工作树，拒绝配置中隐式接管旧任务，列出需要逐个迁移的旧任务 ID。当前它不开放激活，`--run` 明确返回 `WORKER_ACTIVATION_NOT_AVAILABLE`，不会监听 HTTP 或启动 adapter。上架 agent 目前仍需原审核/业务 HTTP 通道；在独立通道和各模板端到端回归完成前，不能用假端口把此入口当作可执行 worker。

上架、下架、利润三个模板的 adapter、批准回执、UNKNOWN 恢复和逐模板启用门禁见[任务模板 worker 准入审计](audits/WORKER_TEMPLATE_READINESS_2026-09-22.md)。该文件是带日期的候选源码审计；实际运行版本、任务库及外部结果仍须现场核对。

`shared_platform/operations_worker_post_policy.py` 记录七条原审核 POST 的调用者与缺失合同；其判定目前全部拒绝，且没有接入 web-only 路由。首轮分类选项、采集与准备属于批准前的准备操作，不需要伪造“用户已批准”字段，但仍缺服务端任务租约和版本绑定；冻结、图片选择及最终批准须继续由原域验证用户回执。仅在请求体添加可伪造的 task ID、版本或批准字符串不能使 POST 安全，必须在域入口用任务库和原批准记录核验，之后才能逐路径开放。

首轮分类 `options`/`capture` 的任务侧校验组件见 `shared_platform/worker_category_admission.py`，严格绑定当前 release、有效执行租约、执行者、Offer 和 facts 步骤，并从精确请求生成稳定请求 ID。候选代码已将任务侧一次性意图、域请求的可信来源、同任务 options 回执及离线服务端桥接接通，并用假官方 GET 验证超时、并发和重启不重发；私有进程管道仍只是原型。正式 49289 仍为 web-only，分类 worker 未启用，不能把离线测试当成线上执行回执。接口合同和剩余门禁见 [Worker R1 分类接续审计](audits/WORKER_R1_CATEGORY_CONTRACT_20260922.md)。

候选事实准备 agent 仅接受父进程核实的首轮分类 capture 证据文件与摘要，不再把网页端口作为可写分类接口；没有可信 capture 时在启动 agent 前阻断。由 agent 选择分类后再由父进程完成官方 options/capture 的第一阶段仍未接线，`get_runtime` 也未注入正式 transport。私有分类请求现要求目标包含在非空任务 `scope.shops` 中；任务未冻结目标时先阻断，后续仍须建立 Product Center 选择与任务范围的服务端冻结来源。

离线 `shared_platform/worker_category_intents.py` 会先在任务库持久登记每任务/动作唯一意图，只有首次登记给出一次派发资格；中断后即使域库找不到请求也不能自动补发，必须按原请求 ID 只读对账。capture 只有引用同任务已核实的 options 与相同上下文才可登记。候选 `ReleaseStore` 已将私有 task-origin 与新域请求放在同一事务，旧人工作业请求不能被 worker 接管；`worker_category_bridge.py` 和 `worker_category_pipe.py` 在隔离测试中验证了调用与进程间协议。正式 worker 入口的生命周期/版本准入尚未接线，三模板未完成全链路验收，故现有审核 POST 继续拒绝。

任务列表和 `/api/orbit/operations-runtime` 提供调度器状态：`stopped` 表示未启动，`source_mismatch` 表示代码或 Skill 与启动版本不符，`error` 带最近错误类别与时间，`polling` 表示最近一轮调度成功。仅在调度线程存活且状态为 `polling` 时，网页才把当前执行器租约显示为“已连接”；短期租约不会掩盖调度错误。错误类别不含凭据或原始异常文本；`polling`、进程存活和 executor 注册均不证明某个业务任务完成，仍需任务及提供方回执。

任务首页还显示未完成回读的历史域操作及其锁定资源数，来自同一任务账本，只作运行诊断。即使原任务已取消或当前版本不再执行，锁也不会因页面刷新、重启或超时自动释放；必须用对应外部平台的真实回读对账。该提示不把历史操作改列为用户待审批任务。

## Windows 网页服务常驻

截至 2026-09-22，本机部署使用 Windows 计划任务 `OrbitHive-Operations-Web` 在用户登录时启动固定版本的 `scripts/operations_web_entry.py`。它只接受 `execution_mode=web-only` 的部署文件，核对部署的 `code_root` 与自身工作树一致，并将启动异常写入指定日志。该计划任务没有周期触发器，也不会连接业务执行器或恢复历史任务；运行版本必须再由 `/api/health` 与 `/api/orbit/operations-runtime` 现场确认。

升级前先在隔离端口及任务数据库备份上验收，再核对原端口的实际 PID 和版本，确保同时只有一个正式 49289 监听。切换计划任务时保留旧部署文件与日志作回退，并在新端口检查任务数、任务/事件账本指纹、五个业务页面和 executor 状态。计划任务“正在运行”只能证明进程托管，不能证明业务执行或平台结果。
