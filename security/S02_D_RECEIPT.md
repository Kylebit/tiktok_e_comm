# S02-D Windows 首次锁文件并发修复回执

状态：提交供 root 限定验收；这是隔离候选，不是生产部署或全系统验收。

- 精确父提交：`cdcf7e002ea30f3cf0b2a96721e68d5c68bdc9ab`。
- 新树：`C:/Users/Windows11/Desktop/Agent_PR/outputs/orbithive-s02-lock-20260905`；分支：`codex/orbithive-s02-lock-20260905`。
- 最终提交与 clean 结果由外部 `C:/Users/Windows11/Desktop/Agent_PR/outputs/orbithive-audit-20260905/security/lock-validation/finalization.json` 记录，避免回执自引用提交哈希。
- 生产仅修改 `modules/sourcing/image_generation_checkpoint.py:94–155` 的 `business_lock`；另新增 `tests/test_business_lock_process.py` 和本回执 MD/JSON。模块其他 AST 完全一致，包括 S02-C 微秒判断及 R2/UNKNOWN 消费规则。源 blob `b3a9f5339dfa6ff2f4b88590250a64cf105be86e` → `91cd7ed4a2e5db2e49308f7809404534cb7c3ef4`。

## 实际修正

去掉取得内核锁前的 `seek/tell/write/flush` 初始化。锁仍作用于同一路径的第 0 字节、长度 1；空文件和已有 1/2 字节文件都保留，不删除、不截断。线程互斥、超时预算、进程退出释放方式不变。释放阶段分别尝试 unlock 和 close，并在 finally 释放线程锁；已有异常保持主异常，额外清理失败写入 exception notes；没有已有异常时上报第一个清理失败。

Windows CRT 允许锁定 EOF 之外字节，故空锁文件无需写入占位。此合同已于 2026-09-05 核对 [Microsoft `_locking`（MSVC 170）](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/locking?view=msvc-170)，并在本机 Windows 10.0.22000、Python 3.12.8 / MSC 1942 x64 的真实 `business_lock` 进程中验证。追加模式会在写前重新定位 EOF，见 [Microsoft `_open`](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/open-wopen?view=msvc-170)。锁定字节也会拒绝同进程第二句柄访问，见 [Windows byte-range locking](https://learn.microsoft.com/en-us/windows/win32/fileio/locking-and-unlocking-byte-ranges-in-files)。完整 URL/版本/源身份在 `source-review.json`。

## 红例的证据层级

1. C4 原失败来自未替换的真实 generator 双进程，第二 worker 在旧第 109 行 flush 报 PermissionError。原测试只保有 pytest 序列化/截断 stderr，没有独立完整 stderr；不得用新记录补造原记录。原始日志、命令、lock 退出后大小 2 / `3030` 的只读 SHA 清单保存在 `original-evidence-preservation.json`。
2. S02-D red1/red2/red4 的原 Python 流屏障固定两个进程都看见空文件，结果是两者成功和 `3030`，**没有复现原 PermissionError**。
3. red4 的 `native-append-gap` 使用测试专用 flush 适配器，在同一真实句柄的 EOF 定位与实际 Win32 WriteFile 之间暂停 B，让实际 `business_lock` 的 A 先持有内核锁。Windows 返回 `WriteFile=false / bytes=0 / WinError=33`，两进程 PID `65032` / `64572`，退出 `[0,1]`，B 尚未进入临界区。测试没有注入 PermissionError；这是具体底层竞争机制证据，**不等同于原 Python BufferedRandom 内部时序已被确定性捕获**。

完整新机制记录：`C:/Users/Windows11/Desktop/Agent_PR/outputs/orbithive-audit-20260905/s02dt-red4/t1` 中 `process-evidence.json`、`native-write.json`、`A/B-result.json`、`A/B-stdout.txt`、`A/B-stderr.txt`。red4 原测试源快照已依据当时 guard 的 SHA 精确还原并核对，保存在 `red4-test_business_lock_process.py.txt`；基线模块 Git 字节在 `base-image_generation_checkpoint.py.txt`。另两个清理红例明确使用 close 故障注入，只证明异常优先级与释放路径，不作为竞争根因证据。

## 回归与复跑

red4 共 4 失败，green1 同四例全部通过。green1 Win32 场景 PID `65400` / `57500` 均退出 0，B 等待 A 离开，持久锁文件仍为 0 字节，危险写入路径不再发生。原流屏障同样通过。

去重后的最新业务/锁结果 **124 passed、0 failed、0 skipped**：新锁矩阵 13 项 + 原相关内容回归 103 项 + R2 预算 8 项。另 1 个 guard 负测试阻止 5 种操作。不是单次全部通过的虚构总计；`validation-summary.json` 按 file/name 保留来源及覆盖关系。

- 13 项覆盖真实空/1 字节/`3030` 文件互斥、超时后恢复、超时同时 close 异常、线程所有者保护、open 失败释放、unlock/close 双失败保持首异常、body 异常保持及两种双进程时序。
- 103 项包含真实多进程仅一次 create、强制进程退出释放内核锁、SUBMITTING 重启不重发、旧任务恢复和精确微秒边界。
- R2 的 8 项为 B01 52 工作量止于第 40 次、B05 两进程争最后槽位、B06 reserve/submitting/post/raw/ack/completed 六个崩溃阶段；全部是假 provider 和合成输入。

保留的测试修正：red3 子进程 UTF-8 stderr 被父进程默认 GBK 解码失败，red4 明确 UTF-8；`lock-matrix.xml` 有 7 passed / 6 failed，六项失败是测试另开句柄读取自己持锁文件，已把字节读取放到 unlock 后，只重跑这六项 `lock-matrix-corrected.xml` 全通过。没有修改生产逻辑以迁就测试，也没有重复已绿整组。

命令均在审计目录 `<label>-command.json`，执行器是 `run_tests.py`，子进程在应用 import 前校验 `guard/sitecustomize.py`。可复跑最终锁矩阵的命令（需使用新的 label，已有证据禁止覆盖）：

```text
D:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm\.venv\Scripts\python.exe -B C:/Users/Windows11/Desktop/Agent_PR/outputs/orbithive-audit-20260905/security/lock-validation/run_tests.py new-review-label tests/test_business_lock_process.py
```

执行 cwd 必须为本候选；`PYTHONPATH` 由执行器固定到 guard 和本候选，禁 bytecode/pytest cache，basetemp 为审计根 `s02dt-<label>`。D 仅提供解释器/依赖，所有应用模块实际 import 路径核对为 C 新树。无需安装依赖。

## 隔离、消费者与后续组合

所有真实测试进程在启动时禁止网络、DNS、bind、SQLite、未声明子进程及真实配置文件读取；仅放行当前 SHA 的三个本地 worker 入口和本包合成路径。非负例没有 connect/DNS/DB 事件；`content`/`budget` 的 urllib3 导入会探测 `::1:0` 的 IPv6 bind，guard 在系统调用前拒绝，库捕获后继续。准确 blocked 计数为 `{"content": 5, "budget": 15, "negative": 5}`，不是“全部零 blocked”。其本机库来源为 `.venv/Lib/site-packages/urllib3/util/connection.py:114–137`，调用与异常处理已只读核对。没有实际网络、平台、付费或生产 DB 调用，未修改 ACL/系统权限，未触碰已运行服务、预览或 installed junction。

精确消费者源 SHA 与行号在 JSON：checkpoint 的 ownership/reconcile/execute（318/633/771/788），`shared_platform/publication_paid_requests.py` 的 phase/budget/chat 锁（352–825），R2 `prepare_product_images.py:2139/2175` 及 `run_automated_image_qa.py:333`。未修改任何消费者。其他模块 AST、C4 资源和 T00 文件不在改动范围。

当前精确 C4 基线尚无 T00 调用者，本包未读改 T00 WIP。其最终精确 SHA 进入组合后，最低复验：调用真实 T00 执行器的两进程同一合成目标仅一个写入；空/1/2 字节持久锁超时、崩溃及恢复；check-only 对合成 manifest 零写入、零 installed junction、零外部活动。必须绑定当时真实调用路径及 test node，不能拿本包 primitive 测试当已验收 T00。

POSIX 分支未变，但本次未运行 POSIX；没有生产替换、APP WebView 或全系统验收。所有临时证据与预览保留，等待 root 审查后续 D00 新基线。
