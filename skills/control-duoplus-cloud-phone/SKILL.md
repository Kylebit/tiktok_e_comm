---
name: control-duoplus-cloud-phone
description: Inspect DuoPlus cloud-phone devices and prepare exact app installation through the official OpenAPI. Installation defaults to an offline preview and uses existing scoped authorization with durable unknown-state recovery.
---

# DuoPlus 云手机

选择显式 runtime root 和非秘密 tenant profile，再用 `orbit_tools.py` 的 `help duoplus`、`doctor --capability duoplus`、`preview duoplus ...`。这些步骤不读取密钥值、不读注册表、不联网。参数与官方合同见 [api.md](references/api.md)。

设备和应用查询使用 `read duoplus devices|info|status|apps|installed-apps`，是实际外部读取，无需安装授权文件；`install-app` 是外部写入，不能当作设备健康探测。先将明确 device IDs、app ID、version ID 和 package 纳入 preview；对相同冻结范围复用已有用户授权，不能要求重复批准。随后 `execute duoplus install-app` 消费原批准引用并在提交前落 durable ledger。

发生 UNKNOWN 或进程中断时用同 scope 的 `reconcile-install` 查询 installed-apps，禁止重装。官方回读只证明 package 存在，不能证明安装版本或某次未知调用已经成功。不要自造 Android HTTP Gateway、shell、代理、账号或开关机能力。

稳定包与安装校验以完整文件 manifest 为准；安装本 Skill 不携带账号、设备、APK 或旧个人配置。密钥仅由执行阶段读取 profile 指定的进程环境键。
