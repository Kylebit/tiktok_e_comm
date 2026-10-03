# D00-A 桌面统一入口实施回执

状态：IMPLEMENTED_FOR_ROOT_ACCEPTANCE。Windows x64 可移植客户端已实际构建并通过本机 WebView2 验证；本回执不宣称生产服务启动、平台业务执行或整套系统验收。

## 精确来源与交付

- 唯一实施树：`C:/Users/Windows11/Desktop/Agent_PR/outputs/orbithive-d00-app-20260905`。
- 分支：`codex/orbithive-d00-app-20260905`；基线 `58a81e9ded5264fc16d897ef6a023ddbbfb2b2e9`。
- 实施提交顺序：`db1d4ecbbdb55910a4beeb698bc7c7f45258e2c6` → `1444f107ccae6f1c827da607bc0ce88494c79cfa` → `5868a8d7516b32da467e6e27b8beb700c54e19ba`。后两提交仅补只读 renderer 图片等待和现有图片页签诊断。
- 生产/配套源差量共 14 文件、1112 插入/865 删除；逐路径基线 blob、最终 blob、物理 SHA256 见同目录 JSON。写回执前 tracked clean。两份回执独立提交，其最终 commit/clean、提交后自有 fixture PID 和身份由审计目录 `finalization.json` 绑定，避免回执自引用哈希。
- 已读本树适用治理与工单。工单头按 root 当前授权更新为 exact58a/new tree；旧 `orbithive-d00-desktop-20260905` 的 8457 检查点未改。
- 审计目录（下文 E）：`C:/Users/Windows11/Desktop/Agent_PR/outputs/orbithive-audit-20260905/architecture/d00-evidence`。最新用户分工要求已记录：本 agent 只完成本包，后续由可见独立会话执行；交接见 E/`VISIBLE_THREAD_HANDOFF.md`。

## 实际变化和文件处置

| 文件/组 | 结果 |
|---|---|
| `desktop/orbit_desktop_webview.py` | 直接承载同一网站顶层文档，移除 iframe 与重复业务导航；保留 CSP。原生连接菜单负责选工程/运行档案/Python、检查、明确启动、诊断与浏览器打开。使用实际 edgechromium，不静默降级。 |
| `desktop/session.py` | 复用既有 `product_publication_runtime` 和 `runtime_identity`，显式 root/profile/port/page。frozen 不从 cwd、exe 父目录或历史 Python 猜工程。已有 READY 直接连接，不要求另选 Python。 |
| `desktop/startup.py` 与两启动兼容脚本 | 统一 CLI 和有操作说明的错误恢复；原 Tk 管理器退役为同一入口，不再 close→stop_all。`--create-profile` 只创建新的非秘密档案，不读配置或创建 DB。 |
| `scripts/product_publication_runtime.py` | 仅增加 `product_port` 接口，Product Center health/command 使用所选端口；原 8765 默认及其它服务规格保持。 |
| `desktop/renderer_diagnostics.py`、`diagnostic_boundary.py` | 可选应用内部真实 WebView2 截图/文档诊断。专用 `--diagnostic-product-images` 只核明确 Offer 后切已有图片页签并滚到图片，不保存/批准/生成。隔离诊断可声明一个本包 loopback 代理并禁用其它网络/DB/秘密文件访问。 |
| build Python/PS1、manifest/lock | 白名单构建，PS1 是明确 Python 的薄包装。新输出目录，不覆盖旧包；锁定依赖，构建不安装/升级。记录输入、完整分析清单与所有产物 hash。 |
| `docs/desktop-client.md`、`tests/test_desktop_session.py` | 中文安装/连接/档案生成/恢复/回滚说明，随包 README；26 项桌面离线契约与原 7 项 launcher 测试。 |

关闭 APP 不停止后台。网页没有 `js_api` 服务控制桥；同源新窗口留在本 APP，主动公开 HTTPS 外链交系统浏览器，其它本地端口/文件/自定义协议拒绝；下载原生取消并指引用户在浏览器使用原页面。

身份检查既用于初次打开，也用于导航、新窗口和文档请求。单有 NavigationStarting 的早期版本实测仍向异身份目标发出一次 `Sec-Fetch-Dest: document` GET，因此增加 WebResourceRequested Document 阶段复查与本地 409 空响应，最终外来 marker GET 为 0。检测到变化回到运行信息页，可明确重新检查恢复。它是这些边界的身份观测，不是已加载页面的连续认证，更不替代现有业务请求的商品/目标授权。

## 最终可移植产物

- 目录：`C:/Users/Windows11/Desktop/Agent_PR/outputs/orbithive-audit-20260905/d00b8/dist/OrbitDesktop`。需一起保留 `OrbitDesktop.exe`、`_internal`、README。
- EXE SHA256：`356137040e34c82c1031280a43de864ac829d1f2fee185a2f007e6c946736e3e`。
- 91 文件，25,485,084 bytes；13 个明确项目输入：7 个桌面/入口文件、5 个身份模块源、1 个中文说明。完整输出在 d00b8/`build-receipt.json`，分析在 `analysis-manifest.json`；E/`bundle-validation-d00b8.json` 已核每个输入等于最终实施源、每个产物等于清单 hash。
- 15 个固定依赖的官方 URL/下载 SHA256 见 JSON。Python 3.12.8 x64、pywebview 6.2.1、pythonnet 3.0.5、PyInstaller 6.16.0。安装只写 E/`runtime-dependencies`，未改全局或旧 D venv。
- 实际 WebView2 为自动发现的 `152.0.4191.62`。包内 x64 EXE 与 x64 loader 实际运行；三个 loader 架构目录都列入白名单，因为锁定 pywebview 初始化时枚举三目录，不能只凭执行架构删除另外两目录。
- 包不含业务后台、工程 UI、配置、DB、报告或用户媒体。它需要外部完整 OrbitHive Git 工程、Git for Windows、系统 WebView2。READY 服务连接无需外部 Python；明确启动后端才需要用户选择有业务依赖的 Python，并要求所选 profile 的 settings 位于所选工程 `config/settings.json`，防止旧配置回退。
- 外部工程的 247 项 UI 资源（含 224 媒体）与 exact58 基线逐项一致；文本仅规范化 CRLF→LF比较、媒体比较原字节。明细包含路径/blob/hash。未通过删除或覆盖资源缩包。
- 7 份合成 canary（设置、token、DB、日志、报告、用户媒体、缓存）在本树 ignored `outputs/d00-build-canaries` 中保留；完整包字节扫描均未出现 sentinel，敏感文件名排除也通过。没有读取真实敏感文件进行此测试。

## 红绿和证据层级

1. **真实旧 HTTP 红例**：E/`red-health-result.json`、`red-health2.log`，旧桥对无关/false 的真实 HTTP JSON 返回 healthy=true，断言失败。该旧实现已移除，不靠改字符串快照完成修复。
2. **离线契约**：E/`final-junit.xml` 为 33 个去重用例、0 fail/error/skip：桌面26 + 原 launcher7。真实来源/端口/档案拒绝、无 Python 已运行连接、明确启动、错误不杀占用者等在对应测试中检查。先前26重复轮不加总。
3. **实际源码 WebView2**：E/`renderer-final3-result.json`，PID62284，23/23 通过。顶层无 iframe、7 区域、99003 深链/图片、无服务桥、同源新窗口、外链 opener spy、8765 阻断、原生下载取消、同端口身份变化普通导航/新窗口阻断、显式恢复、0业务 POST、关闭仍 READY、持久合成任务 DB hash 不变。E/`renderer-identity-cycle3-result.json` 的8项是较早重叠子集，不加到23。
4. **旧进程与新提交**：E/`artifact-stale-commit/acceptance.json`，b6 EXE PID59712，旧 base58 handler 与已变更磁盘提交 db1 不同，明确 SOURCE_MISMATCH/exit1。这是预期拒绝，未把旧运行进程标成新代码。
5. **最终 b8 EXE 受影响三项**：每次从独立空 cwd 启动，PATH 仅 Git/Windows，无 `--python` 或 Python site-package 环境；实际打开相同原生 APP。证据如下。

| 最终包运行 | PID/退出 | 实际状态和证据 |
|---|---|---|
| `artifact-b8-main` | 52732 / 0 | READY，顶层同一主工作台，0 iframe；原生 CapturePreview PNG 已实际查看。7 个本包 loopback GET、0 POST。 |
| `artifact-b8-images` | 46772 / 0 | READY，URL `/product-workspace?offer_id=99003`、输入99003一致。实际切“图片与内容”，两张不同合成来源图均 complete、natural400×400、viewport可见；截图已实际查看。`image_wait_timed_out=false`。13 个本包 loopback GET、0 POST。 |
| `artifact-b8-missing` | 64840 / 0 | PROJECT_REQUIRED，about:blank 本地恢复页，清晰指向连接菜单和档案生成，0网络请求；退出0仅表示窗口正常关闭，非 READY。截图已实际查看。 |

每项有 E/`<label>/acceptance.json`、`diagnostics/renderer.json` 和 `renderer.png`。图片页的四个 DOM image 是两份来源图在来源/结果区域的展示；本回执按两张不同源图统计。viewport 字段表示与视口相交，截图明确显示上方两个完整来源卡，不以底部少量相交的结果卡代替此证据。

以上是 **actual WebView2 application test interface**，不是普通 Chrome，也不是人工原生窗口/文件选择器验收。通过应用所拥有的 WebView2 接口调用现有页签与原生 CapturePreview；没有使用可控远程调试端口、暴露任意脚本桥或修改业务 UI。最终只重复受诊断代码影响的三小项，没有重跑23/33全套。

## 图片负例和环境失败保留

- 原 `tests/dense_workspace_preview.py` 给 99003 定义 good.png 与 broken.png。它是刻意缺图 fixture，不是真实商品缺图结论。早期 b6 深链截图/JSON第一图400×400、第二图404/width0完整保留于 `artifact-deep-final`；源测试的 fallback 成功不能证明第二原图有效。
- 切正常 fixture mode `valid_images=true` 后，为 broken.png 路径返回第二张不同400×400合成 SVG。早期 `artifact-two-valid` 太早截图出现两空 src/complete=false，ok=false 保留，未当通过。
- 最终诊断等当前图状态稳定至少0.5s且complete，最长5s；显式报告timeout，不把稳定空src或字段数量当图片就绪。另实际打开已存在的只读图片页签后查尺寸与可见性；正常两图成功仅由 `artifact-b8-images` 证明。
- 固定 runtime 文件夹/长状态路径的首轮 0x80070002 原日志保留；系统自动发现 runtime +短路径成功。不把多变量变化断言成单一根因。
- PyPI 首次安装缺构建 setuptools、proxy_tools 长路径失败；已固定依赖并使用本包短构建临时目录成功，原命令/日志保留。没有反复放宽网络权限。
- b1 构建 guard 拒绝 Popen；b5 包构建成功但真实启动报 Cannot find win-arm64。后续明确加入三个锁定 loader 目录，最终 b8 build exit0且真实运行通过。旧包不代替最终验收。
- 早期隐藏 renderer/CDP screenshot超时、过短下载等待、同端口 foreign document GET失败均保留；最终使用可见同一 APP 原生 CapturePreview、等待实际事件以及文档阶段身份拦截。
- 本包 fixture 重启期间 psutil 不存在导致停止失败，随后错误地依赖该步骤启动第二自有进程。确认62148/34676均为本包精确命令后以 Win32终止；后续 fixture 使用独占bind（allow_reuse_address=false + SO_EXCLUSIVEADDRUSE）。原始状态、命令和 WinError10022中间失败保存，不声称当时只有一个监听者；未终止其它预览。最终实施源码预览为 PID5176:56521，回执提交后仅重启该自有 PID，最终PID见finalization。
- 部分原生代理日志有 APP关闭取消GET后的 ConnectionAbortedError10053；保留原日志，不当业务成功。23项源码测试有被本包代理拒绝的外部尝试（例如 example.com CONNECT），它们未出网；最终b8三项没有代理拒绝请求。

## 隔离与未覆盖项

实际 handler 继承既有 U00 真实路由，读本树代码；配置初始化调用数0，使用手工标注来源的合成 cache和生成 profile。仅真实 ReleaseStore 合成数据库允许访问，所有真实配置/凭据/业务DB及泛网络由进程 guard 拒绝。native代理只放行56521声明GET，POST/CONNECT拒绝；图片 URL仅作为合成键，不访问fixture.invalid。

本包依赖安装是已授权官方固定版本下载，和离线运行测试区分。0平台/付费/消息/生产写入、0浏览器启动真实外链，外链消费者以 opener spy验证。正常APP可使用原业务页面，但本包没有赋予任何额外写权限。

显式后台启动通过既有 launcher 的7项离线契约和桌面参数测试验证；真正 renderer 连接的是本包启动的真实handler+合成数据 fixture，未声称在生产配置下由菜单实际运行 main.py。人工原生文件选择器、未安装WebView2的其它机器、签名/快捷方式/远程部署均未验收。已加载业务页面的持续身份认证、生产业务可用性和全部工具安装不是此客户端包的结果。

保留原3000/8788、8765/8766/8767及C4/U00/视频预览，不清理它们。本包最终合成预览保留供root审核；仅最终PID和commit以提交后的finalization为准。

## 可复跑与接续

精确命令包含在 `tests-final-command.json`、`renderer-final3-command.json`、`d00b8-command.json` 和各artifact acceptance JSON。脚本位于 E，所有结果目录要求新标签，不覆盖既有证据；请沿用测试 guard和本包端口，不直接对真实8765运行。

最终包连接示例（已有隔离服务，需根据finalization核对工程/profile）：

```text
OrbitDesktop.exe --project-root C:/Users/Windows11/Desktop/Agent_PR/outputs/orbithive-d00-app-20260905 --profile C:/Users/Windows11/Desktop/Agent_PR/outputs/orbithive-audit-20260905/architecture/d00-evidence/handler-fixture/preview-data/runtime-profile.json --port 56521 --page /product-workspace?offer_id=99003
```

实际公开用户说明见工程 `docs/desktop-client.md` 与包内 `README.zh-CN.md`，有菜单连接及 `--create-profile` 完整命令，不要求手工改JSON。回滚仅关闭客户端换回保留包；不删除工程数据、不停止后台。

后续 C5 工单：`C:/Users/Windows11/Desktop/Agent_PR/outputs/orbithive-audit-20260905/work-orders/WO-20260905-ORBIT-C5-A.md`。由root验收并在可见独立会话固定最终base/新树后执行；本 agent 不启动组合。D00三实施提交加回执提交应按链保留，C4/S02-D不能被T00旧树覆盖，桌面包不能盲拷成源码。

本包使用的官方接口说明：Microsoft [WebView2 threading model](https://learn.microsoft.com/zh-cn/microsoft-edge/webview2/concepts/threading-model) 支持事件/异步调用和避免重入的实现判断；[Runtime.evaluate](https://chromedevtools.github.io/devtools-protocol/tot/Runtime/#method-evaluate) 的userGesture用于本APP明确诊断点击，不冒称人工交互。依赖实际官方来源和版本hash见JSON及pip报告；pywebview具体loader枚举行为按本包锁定6.2.1安装源核实。
