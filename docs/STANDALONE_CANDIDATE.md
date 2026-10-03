# 独立目录审核候选

此页取代UNIFIED_CANDIDATE.md中第一轮预览的路径依赖说明，原文保留为过程来源。

目录根为 `D:/OrbitHive/candidate-20260908`：

- `repository`：独立Git对象库，来自已验c91f880，本包只增加审核依赖锁、外置override参数和本说明。
- `environment`：专用Python虚拟环境，依赖按原已运行版本冻结16包，离线从wheelhouse按SHA256锁安装。Python基础解释器由本机Codex捆绑运行时提供，不依赖旧工程虚拟环境；这不是含解释器的跨机器免安装发行包。
- `runtime/private`：现用目录数据库安全副本及override副本，无凭据。
- `runtime/state`：独立profile、settings与日志；平台发布库/工作台执行库未连接。
- `wheelhouse`：锁定依赖的本地可重建安装源。

在确认49298未占用后，从目录根执行：

```bat
environment\Scripts\python.exe -I -B -X utf8 repository\scripts\catalog_review_preview.py --metadata runtime\DATA_SNAPSHOT.json --state-dir runtime\state --profile runtime\state\runtime-profile.json --weight-overrides runtime\private\weight_overrides.json --port 49298
```

该命令以前台方式运行，已有候选进程时不要重复启动。审核地址为http://127.0.0.1:49298/catalog。`--weight-overrides`只允许读取与审核数据库同一private目录内的副本，不改变普通服务默认路径；成本保存仍只影响副本。预览没有平台执行配置，不把页面可打开当全业务就绪。

依赖重建命令（在已创建同版本Python环境后）：

```bat
environment\Scripts\python.exe -m pip install --no-index --find-links wheelhouse --require-hashes -r repository\requirements-review.lock.txt
```

新仓库的origin仅是本地来源引用，不影响对象独立性，不要求访问旧目录才能读取当前历史。无alternates、无Git对象硬链接。最终替换正式目录或更改远程地址尚未执行；旧树及49289保持原状。

补充核验：源码历史中确有已跟踪的 `data/weight_overrides.json`，第一轮说明称其为忽略文件不准确。该历史文件保持原blob，本候选通过显式参数读取runtime/private副本，不依赖代码目录内的旧默认值。
