# 个人 Skill 的窄同步与利润完整工程入口

本包只同步四个明确脚本：准备1、图片2、下架1。`scripts/sync_personal_native_workflow_scripts.py` 默认只核查，apply 前备份原字节和路径身份，所有目标/依赖 SHA 均来自显式 JSON plan；拒 reparse/hardlink/漂移。供应链 junction 不重指、不覆盖，发布和折扣只有换行差异不写。原正式工程、配置、数据库与服务不变。

已安装脚本的 NativeImageRuntime、call_guard、官方 client 等能力需要完整工程。个人目录匹配不等于 provider 准备就绪，不从 REPO_ROOT 猜另一个项目。实际工作优先通过平台精确新任务，或引用明确完整 runtime 原入口，不直接从个人目录发起默认 CLI。

## 利润安装方案

真实 slug 是 `manage-profit-settlement`。个人目录起初缺失，现已按下面的 Junction 方案安装。其原 scripts/profit_report.py、月度 builder 必须找到父链 `domains/data_operations/profit_settlement`，整树 copy 到个人目录不能提供这个条件。

可用的最小个人安装方案是将这个新 slug 建为同 Windows 用户可核验的 Junction，明确指向选定完整 runtime 的 `domains/data_operations/skills/manage-profit-settlement`，安装前核目标完整 SHA、目录身份与个人目标不存在，保留 junction 原路径/Target 回执。这样原脚本的 resolve() 才落回完整领域工程。当前正式16d源正在使用必须保留，不自动归档。2026-10-03 已按此方案实际创建新个人 Junction，完整21文件 raw/normalized 与冻结16d匹配，原入口 resolve() 与父链均落回完整16d工程；未导入域/provider或执行利润任务。回滚仅移除此新 Junction，不能递归删除其目标。

仓库中的 `scripts/repo_bound_profit_entry.py` 要求明确提供 runtime 根和原 runtime manifest SHA，以及独立利润依赖 manifest 的绝对路径和预期 SHA。原工具 runtime manifest 不覆盖利润源，不能单靠它证明完整利润代码。`config/profit_entry_dependencies16d.json` 把原18路径按16d重新哈希，并覆盖完整 Skill 及四个入口的静态本地导入闭包，共371个文件（不扩大原工具manifest范围）。核验每个预期原始字节 SHA、无 reparse/hardlink 和目标范围，执行前再次读取；所有拒绝为显式错误，`-O` 不能绕过。`--check-binding` 只核文件，完全不导入域/provider。

最初顶层源码白名单漏列 `tiktok_settlement.py`，现已核实它是冻结16d tracked纯源码（原始工作区字节与HEAD只有换行差异），补入独立源码提交，不导入旧历史。371个依赖现与16d逐raw匹配。该清单须用于明确完整 runtime，Python第三方依赖、结算/成本/FX/店铺资料与凭据另按任务核验；源文件匹配不是业务准备就绪，也不会给个人原脚本添加新权限。入口只选择原 report、TikTok monthly、Shopee monthly、weekly 脚本，将显式参数原样交给原脚本；不自动计算/批准、不补月份/店铺/成本/FX，不授新历史任务权。

例如先对部署实际 runtime manifest 文件计算 SHA，然后：

```text
python -I -B scripts/repo_bound_profit_entry.py --runtime-root <完整固定root> --expected-runtime-file-sha <runtime文件实际SHA256> --profit-dependency-manifest <独立利润清单绝对路径> --expected-profit-dependency-sha <利润清单已核SHA256> --check-binding
```

真实任务仍核完全结算截止、原费用/成本/FX证据和配置。UNKNOWN 不二派，历史 July 报告保持原字节；个人文件安装不改变业务执行授权。目录无依赖时给具体错误，不伪造可用或建立空 paid baseline。
