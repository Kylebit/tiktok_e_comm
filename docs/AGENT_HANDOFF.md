# 从当前工单开始

先按 [AGENTS](../AGENTS.md) 核验当前根、提交、owner 和范围，再选下面一个入口。`R` 是已核验的完整源码或解压 runtime 绝对根，`P` 是本工单项目根，`S` 是显式 Skill 目标根；把占位符替换为实际路径。`python` 代表已确认存在的解释器，不要求为接手创建新环境。

## 三个特殊只读场景

本页只服务发布关闭 runtime、portable 新项目和浅目录 Skill check，不是所有新 agent 或领域任务的必经入口。普通任务按当前 Work Order 与[派单恢复段](pm/DISPATCH_CONVENTION.md#resume)接管。

### 发布关闭 runtime

`publication-closure` 的**业务服务端**仍需完整 OrbitHive runtime 和原始批准、发布证据，因此能力目录标为 `WORKFLOW_RUNTIME_REQUIRED`。[关闭脚本](../skills/publish-approved-product/scripts/close_product_publication.py) 则是仅用 Python 标准库的 loopback 客户端；portable 包内副本可独立显示 `--help`，但不能独立产生业务关闭结果。先核验目标服务与精确证据，不能从个人 Skill 安装或 portable 包补造服务端、批准或报告。

客户端要求显式 `--base-url http://127.0.0.1:PORT --expected-root R`（也接受 `localhost` 或 `[::1]`），并通过 `/api/health` 核对服务身份、绝对根与缺失依赖。`doctor` 只证明运行时身份和依赖，不证明业务授权；`prepare --input INPUT.json` 预览精确计划与目标证据；`record --prepared PREPARED.json` 由服务端复验并写入不可变本地回执；`latest --offer-id OFFER --plan-id PLAN` 读取同一计划的最新有效回执。输入格式和结果不明的恢复方式见[关闭参考](../skills/publish-approved-product/references/closure.md)。关闭不发布商品；`record` 结果不明时保留预期 closure ID/digest，先读 `latest` 对账，不自动重试。

### 新项目 portable

从[工具入口](tools/README.md)确认包版本、依赖与 profile 字段。普通 portable 能力的 help 不需要项目配置；doctor 需要先把包内 `config/tool_profile.example.json` 作为非秘密模板保存到本工单 `P/profile.json`，明确 tenant 和产物路径，再按该能力的目录命令运行。

需要把七个完整业务 Skill 交给新 agent 或新项目时，先看[一页式来源、安装身份和离线预览卡](tools/SEVEN_SKILL_PORTABILITY.md)；它们不随 portable 工具包分发。

`publication-closure` 是例外：portable 客户端的 `--help` 和无配置的 `CONFIG_REQUIRED` 检查可运行；能力目录的 doctor 仍会因缺少完整业务服务端模块报告不可用。只有显式指向已核验完整服务端的客户端调用才能检查或记录关闭，不能把便携客户端可运行等同于业务能力可用。配置不包含旧店铺身份、真实数据或密钥。`docs/tools/README.md` 随包分发，本文属于完整源码接手文档；包内无需存在 AGENTS 或完整 R3 Skill。

### 浅目录 Skill check

目标尚不存在也可先诊断；用本工单已授权的浅输出根替换 `S`，不默认个人安装目录：

```text
python -I -B -X utf8 R/scripts/sync_product_publication_skills.py --registry --runtime-root R --destination-root S --skill use-lingshi-ai --check
```

未安装时一致性为 false、退出 1，不会自动安装。长路径按[工具路径预检](tools/README.md#write-paths)处理；check/preview 通过只说明对应本地检查，安装仍依当前任务范围。用户已指定精确目标时不静默换目录；恢复已有备份须核验完整 manifest，保留部分文件。

## 经失败证据支持的恢复入口

需要整理 SOP、事故、补货或利润经验时，从[知识使用指南](knowledge/USAGE.md)选择场景；固定版本导出与检索沿用现有 CLI，不把笔记当当前业务事实。

| 观察 | 下一步及边界 |
| --- | --- |
| shell 报 `Your Windows doesn't fully support CET`，或中文输出解码失败 | 在当前权限内选已可用的 `cmd.exe`、非登录 shell 与现有 Python `-X utf8`；先执行最小命令。不是修改 ACL、沙箱或系统策略的理由。 |
| Git 因仓库所有者不同拒绝只读命令 | 核验精确 root 后，对单条命令使用 `git -c safe.directory=<EXACT_ROOT> ...`；不修改全局配置，不设通配信任。 |
| Windows root 表示差异、祖先 junction/link 或深路径错误 | 保留输入的原始绝对 root，交给同包校验器；不要先 resolve 丢失 alias。支持范围、浅目标、部分备份与错误码只见[路径预检](tools/README.md#write-paths)。S04 的回归是本地工具边界，未证明任意 TOCTOU 或全面长路径支持。 |
| 原批准文件损坏/缺失 | [恢复脚本](../scripts/recover_publication_approval.py)默认 inspect；只有完整原证据才能恢复原内容。缺原证据限制依赖该批准的动作，不要求为继续本地修复补造或重批。 |
| paid/write 已可能发生，结果 unknown | 按[唯一恢复规则](THREAD_OPERATING_MODEL.md#recovery)恢复 durable 身份并对账；closure 按其参考核对 ID 与 digest。文档去重不解除 unknown 或预算合同。 |
| 测试重复红/guard 事件/源码版本不明 | 按[证据口径](TESTING_GOVERNANCE.md#evidence)保存原失败、实际导入来源和拒绝事件，再做有界相关复验；不以历史绿测或去掉 guard 宣称通过。 |
| 宿主拒绝或任务权限暂停 | 按[宿主权限](THREAD_OPERATING_MODEL.md#host-policy)记录真实拒绝，推进不依赖部分；项目文案不会改变应用权限或恢复暂停任务。 |

详细诊断保留在对应原 Work Order 回执，不抄入常驻指令。当前源中的实际回归入口包括[批准恢复](../tests/test_s03_approval_recovery.py)、[portable 关闭](../tests/test_portable_closure.py)、[祖先校验](../tests/test_s04_ancestor.py)与[长路径恢复](../tests/test_s04_longpath.py)；找不到时先核对精确候选，不能从旧 checkout 推断未交付。
