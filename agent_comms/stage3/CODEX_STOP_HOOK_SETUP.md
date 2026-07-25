# Codex Stop-Hook 自驱链 · 部署与测试手册

> 目标：让在跑的 Codex 会话在每轮结束（Stop）时自动从 `codex_inbox/` 抓取下一个任务续跑，
> 实现背靠背任务**零轮询延迟**。这是比"纯 2 分钟定时器轮询"更好的事件直驱近似方案。
> 定时器（Thread Automation）降级为"首发唤醒 + 会话兜底"，不再当主执行者。

---

## 一、前置确认（✅ 2026-07-21 已官方确认 + 生产自测通过）

- **已确认**：桌面 App 走用户级 `~/.codex/hooks.json`；`Stop` 是 turn-scoped 事件（非 CLI 专属）。官方文档 + 实机均验证通过。
- **⚠️ 装好 hooks.json 后必须重启 Codex App** 以重新加载配置（否则钩子不生效）。
- **⚠️ 首次运行 App 会弹「命令信任」提示**，需批准我们的 `codex_stop_selfloop.py` 钩子命令（一次性信任）。
- 确认 Codex 版本较新（本机 gpt-5.6-terra，远高于 v0.124），hooks 默认启用、无需 `codex_hooks` 特性开关。
- **cwd 作用域**：钩子已硬编码为「仅当 Stop 事件 cwd 含 `tiktok_e_comm` 才动作」，避免你用 Codex 聊私事时误抓 Orbit Codex 任务。故请确保 Orbit Codex worker 会话的工作目录在该仓库内（config.toml 已 `trust_level="trusted"` 该 project）。

---

## 二、注册 Stop 钩子

编辑（或新建）`C:\Users\Windows11\.codex\hooks.json`：

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "C:\\Users\\Windows11\\.workbuddy\\binaries\\python\\versions\\3.13.12\\python.exe \"C:\\Users\\Windows11\\Desktop\\Agent_PR\\tiktok_e_comm\\agent_comms\\stage3\\codex_stop_selfloop.py\"",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

若 hooks 不生效，在 `C:\Users\Windows11\.codex\config.toml` 加（老版本需要）：
```toml
[features]
codex_hooks = true
```
或运行一次 `codex --enable codex_hooks`。

> 钩子脚本仅用 Python 标准库，用绝对路径的 python 保证一定跑得起来。

---

## 三、Thread Automation 定时器（⚠️ 已降级：仅「冷启动踢动」，非执行主路径）

**架构已证明是真·事件驱动（Stop 钩子负责执行与链式续跑），2 分钟轮询执行不再需要。** 定时器只解决一个边界情况：**当 Codex 完全空闲、没有任何 turn 在跑时，新任务到达需要"一轮 turn 结束"才能触发 Stop 钩子**——此时需要一次轻量踢动。

在 Codex App 给 Orbit Codex 线程建一个每 **2~3 分钟** 的 Thread automation，prompt 改为**纯踢动**（不自己执行任务，执行交给钩子，避免双跑）：

```
每 2 分钟执行一次：检查 agent_comms/stage3/codex_inbox/ 下有没有 status 不是 DONE/BLOCKED/CANCELED/FAILED 的任务。
- 如果有：只在对话里简短回一句「inbox 有 N 个待处理任务，等待 Stop 钩子续跑」，然后结束本轮（不要自己动手执行）。
- 如果没有：回「idle」然后结束本轮。
注意：不要在这里直接执行任务，真正的执行由 Stop 钩子接管。
```

> 若你平时一直开着 worker 会话并常有交互，连这个轻量定时器都可以不要——手动发一条消息即可触发 Stop 钩子。定时器只是"无人看管时"的保险。

---

## 四、自测步骤（✅ 已生产自测通过，以下为复盘/复测用）

1. 测试任务已放置：`codex_inbox/task-stopselfloop-test.json`（status=ACKED_LOCAL_INBOX）。
2. **重启 Codex App** 让 hooks.json 生效；首次运行若弹「命令信任」提示，批准 `codex_stop_selfloop.py`。
3. 在 Codex（App，**worker 会话 cwd 需在 `tiktok_e_comm`**）里发任意一条消息作为踢动（例如「开始 inbox 自驱链」）。
4. Codex 结束该轮 → 触发 Stop 钩子 → 钩子发现 `task-stopselfloop-test` → 以 `decision:block` 注入**任务文件绝对路径** → Codex 读文件执行。
5. Codex 自动续跑该任务 → 运行 `--report` 回报 → 再 `--complete` → status 变 `DONE` → 下一轮 Stop 钩子将其移入 `archive/`。

**已验证结论（2026-07-21）**：Boss 在 App 向 Codex 提问那一轮结束即触发钩子，自动抓取并执行了 `task-stopselfloop-test` 至 DONE+归档。**真·事件驱动零轮询链路实锤跑通。**

**复测判读**：
- Codex 自动从踢动消息续跑并执行测试任务 → ✅ 链路通。
- 只回"idle"/没续跑 → 钩子没触发：检查 App 是否重启重载 hooks.json、命令是否已信任、worker 会话 cwd 是否在仓库内。
- 续跑但重复抓同一任务 → 检查 `claimed_at` 标记与 30 分钟 stale 逻辑。

---

## 五、与现有架构的关系

- adapter (`codex_adapter.py`)：保持**纯桥**（写 inbox + ACK），不执行，也不打印 TICK 唤醒（SSE/`--stream` 唤醒已证实不通并彻底弃用）。
- 派发：`POST /dispatch` mode=direct 仍把任务落 `codex_inbox/<id>.json`。
- 回报：Codex 仍用 `--report` / `--complete` 回 `/ingest`（不变）。
- 本方案核心：新增"Stop 钩子从 inbox 自驱续跑"这一层，是真·事件驱动主路径；Thread Automation 定时器仅作冷启动踢动兜底。

---

## 六、未走通的对照（备查）

- `codex-agent`（第三方）：notify 出站 + tmux/OpenClaw 路由，非唤醒入站，不采用。
- `codex exec` + webhook 监听器：真·事件驱动理想方案，但需子进程拉起 `codex`，
  本机受 TrustedInstaller/ExecutionPolicy 墙限制（Boss 不动权限），暂不可行，留作未来。
