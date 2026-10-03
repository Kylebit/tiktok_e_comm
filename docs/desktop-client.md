# OrbitHive 桌面客户端

APP 打开所选工程的同一套 Web 页面。任务首页、商品目录、商品上架、供应链、利润、知识工具五个一级入口，以及商品队列和审批规则均与 Web 共用；关闭窗口会保留后台服务和任务。

此包是 Windows x64 客户端，包含 Python 和桌面依赖。它不包含业务服务、工程资源、配置、数据库或用户图片。使用前需要一份完整 OrbitHive Git 工程、Git for Windows 和 Microsoft Edge WebView2 Runtime。连接已经运行的服务不需要另选 Python；只有明确启动后端时才需要工程使用的 Python 及其业务依赖。

## 打开已有服务

保留 `OrbitDesktop.exe` 与整个 `_internal` 文件夹在同一目录。双击 EXE 后，通过顶部“连接”菜单依次选择工程文件夹和非秘密的运行档案，再选择“重新检查并打开”。客户端不会根据当前目录猜测工程，也不会自动启动后端。

也可以在命令行或快捷方式中固定选择，所有文件位置必须使用绝对路径。例如：

```powershell
& 'C:\OrbitDesktop\OrbitDesktop.exe' --project-root 'C:\OrbitHive' --profile 'C:\OrbitHive\config\runtime-profile.json'
```

商品深链接使用同一入口内的路径：

```powershell
& 'C:\OrbitDesktop\OrbitDesktop.exe' --project-root 'C:\OrbitHive' --profile 'C:\OrbitHive\config\runtime-profile.json' --page '/product-workspace?offer_id=明确的商品ID'
```

默认服务端口为 8765；隔离环境可加 `--port 49199`。默认客户端状态位于 `%LOCALAPPDATA%\OrbitHive\desktop`，可用 `--state-dir` 指定其他可写绝对目录。不要共用正在打开的 WebView2 状态目录。

## 准备运行档案

优先使用此工程已有的 `runtime-profile.json`。运行档案只有配置位置和数据位置，用来核对服务身份；它不会搬运数据、改变后台实际使用的数据位置，也不是 `settings.json` 或 token 文件。

首次准备时，先从已确认的工程配置和部署记录确认下面各位置，然后运行一次生成命令。示例位置必须替换成该工程的实际位置：

```powershell
& 'C:\OrbitDesktop\OrbitDesktop.exe' --create-profile `
  --profile 'C:\OrbitHive\config\runtime-profile.json' --profile-id 'local-operations' `
  --settings-path 'C:\OrbitHive\config\settings.json' `
  --catalog-store 'C:\OrbitHive\data\shop.db' `
  --report-store 'C:\OrbitHive\data\orbit_platform.db' `
  --workbench-store 'C:\OrbitHive\data\orbit_workbench.db' `
  --ozon-dir 'C:\OrbitHive\modules\ozon\legacy_webapp\data'
```

该命令只创建新的运行档案，不读取设置内容、不创建数据库，也不覆盖已有文件。档案中的位置必须与后台实际使用的位置一致；“数据目录不一致”需要先核对位置，不能以新建空数据库消除提示。

## 启动与恢复

- **服务未启动**：在“连接”中选择明确的 Python，再选择“启动所选服务”。也可通过项目自己的受控启动方式启动，然后在 APP 重新检查。APP 仅启动所选 Product Center 服务，不自动启动 8766/8767 等其他服务。
- **端口被占用／连接到其他服务**：检查所选端口、工程和运行档案。APP 不会停止占用者。
- **工程或版本不一致／资源不完整**：恢复完整工程或由服务负责人重启明确归属的服务，再重新检查。旧进程不能以当前磁盘的新版本冒充已更新。
- **运行档案／配置尚未准备**：选择非秘密的运行档案。显式启动要求该档案指向所选工程的 `config/settings.json`，防止意外退回历史工程。APP 不创建凭据。
- **WebView2 无法启动**：检查 Microsoft Edge WebView2 Runtime 与客户端状态目录权限。源码方式还需要桌面依赖；EXE 中已包含这些 Python 依赖，不会静默切换到另一种服务管理界面。

“已连接”仅表示服务身份匹配，不表示商品已批准、已发布或付费任务已完成。顶层导航、新窗口以及文档请求会重新检查身份。业务写入与结果以原页面的当前商品和目标合同为准。

同一服务的新窗口链接在当前 APP 打开。用户主动打开的公开 HTTPS 外链交给系统浏览器；其他本地服务、文件或自定义协议不会装进 APP。APP 不提供页面可调用的启动/停止服务桥。下载在 APP 中取消；需要下载时，使用顶部“连接 → 在浏览器中打开”，再操作该页面的下载入口。

## 只读诊断与源码构建

无窗口检查可使用 `--status --result 'C:\Temp\orbit-status.json'`，并同时传入工程、运行档案和端口。结果文件必须是新路径。退出码 0 表示 READY；非零时读取具体状态。

实际 WebView2 诊断使用 `--diagnostics 'C:\Temp\orbit-renderer' --exit-after-diagnostics`。它会显示同一个 APP 窗口，记录当前文档、图片尺寸、运行身份和渲染截图后关闭；不点击业务按钮。诊断通过应用内部的 WebView2 接口完成，不等于人工原生 UI 验收。隔离测试可追加 `--diagnostic-proxy-port`，配合测试方声明的 loopback 代理禁止其他网络及数据库访问；普通使用无需该参数。

从源码启动：用已安装桌面依赖的明确 Python 执行 `scripts/start_orbit_desktop.py`，参数与 EXE 相同。源码默认工程仅由脚本所在目录确定；EXE 必须选择工程。

构建前，在独立环境安装 `desktop/requirements-build.lock.txt` 锁定的依赖，不修改全局环境。构建脚本不会安装或升级依赖：

```powershell
& 'C:\BuildEnv\python.exe' -B 'C:\OrbitHive\scripts\build_orbit_desktop.py' --output 'C:\OrbitBuilds\build-001'
```

输出目录必须是新目录，已有构建不会被删除或覆盖。`desktop/build_manifest.json` 列出允许进入包的项目源文件与 WebView2 资源；`build-receipt.json`、`analysis-manifest.json` 记录输入、依赖来源和输出 SHA256。三个架构的 WebView2Loader 文件均保留，是因为锁定的 pywebview 在初始化时枚举三个目录；本包可执行程序和实际加载的 loader 仍为 x64。

回滚客户端时，关闭本窗口并从保留的上一版客户端目录重新打开。不要删除工程、配置、数据目录或停止独立后台任务。
