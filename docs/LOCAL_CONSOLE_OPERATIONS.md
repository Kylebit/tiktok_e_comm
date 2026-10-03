# 唯一开发候选与本地运行

当前持续开发分支为`candidate/main`，根为`D:/OrbitHive/candidate-20260908/review-repository`。该根绑定395审核登记，不移动或复制registry。`repository`仅为独立common Git的detached辅助checkout；原standalone分支保留为archive，不继续开发。8b2354已包含refs、中文文档及负例，无需重复迁移。旧正式D工程不动。

执行器为本分支 `scripts/local_console.py`。默认仅dry-run，只有`--execute`执行本地操作。外部config列明唯一code_root、expected_head、expected_running_head、state_dir、metadata、override、port、profile_id、review_offer及rollback_receipt；不包含凭据。变更HEAD后必须更新审核配置，不猜测运行身份。

```bat
environment\Scripts\python.exe review-repository\scripts\local_console.py status --config <本次配置>
environment\Scripts\python.exe review-repository\scripts\local_console.py start --config <本次配置> --execute
environment\Scripts\python.exe review-repository\scripts\local_console.py restart --config <本次配置> --execute
environment\Scripts\python.exe review-repository\scripts\local_console.py backup --config <本次配置> --backup-dir <全新backups子目录> --execute
environment\Scripts\python.exe review-repository\scripts\local_console.py rollback --config <本次配置>
```

最后一条仅展示回滚计划；核对旧command/profile与同一catalog后才加`--execute`。重启前核health、实际监听PID及命令根，不按固定PID杀进程。启动失败记录日志，不宣称回滚已发生；rollback支持服务已退出的恢复场景，不改Git历史。实际服务目录映射在本次外部配置与OPERATOR_RECEIPT中，不能把历史截图端口当现状。

回滚配置还必须包含预先冻结的`rollback_binding`：root、HEAD、receipt_sha256、command_sha256，以及旧启动脚本/metadata/profile/override的file_hashes。HEAD应来自被接受的旧部署回执，不从执行时旧树当前HEAD推定。工具在任何服务查询/停止之前核这些指纹、目标HEAD与干净工作树；漂移即拒绝，不自动重冻或继续停服务。

备份使用SQLite在线API，源query_only事务，target完整性/全部表计数和override前后hash。静态快照位于新受控目录，零源SQL写，不checkpoint，不宣称零sidecar文件变化。已有回执/备份不覆盖。

本包只收敛启动与路径，不调整商品正常入口或利润UI；那些修复由各自任务继续交付。正常入口的完整用户流程需另行验收，395直链通过不能替代首页发现与完整阶段验证。
