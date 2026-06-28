# Git 同步：TikTok MX / UK 迁移代码

供 **Codex / Cursor / Claude** 在 Windows 或 Mac 上把 MX、UK 迁移相关改动推送到 GitHub。

## 仓库

| 仓库 | 用途 | 远程 |
|------|------|------|
| `Kylebit/tiktok_e_comm` | MX/UK 业务代码、脚本、Web 审批页、定价配置 | `https://github.com/Kylebit/tiktok_e_comm.git` |
| `Kylebit/orbit-hive-agent-ops` | 飞书协作规则、任务协议、agent  onboarding | `https://github.com/Kylebit/orbit-hive-agent-ops.git` |

**MX / UK 代码只进 `tiktok_e_comm`**，不要写进 `orbit-hive-agent-ops`。

## 本地目录（Windows，Cursor 当前）

```text
C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm
C:\Users\Windows11\Desktop\Agent_PR\orbit-hive-agent-ops
```

Codex 应使用**独立 clone**，不要与 Cursor 共用同一工作目录（见 `orbit-hive-agent-ops/README_FOR_AGENTS.md`）。

## 开始工作前

```powershell
cd C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm
git pull origin master
```

若 Git 报 `dubious ownership`，加：

```powershell
git -c safe.directory=C:/Users/Windows11/Desktop/Agent_PR/tiktok_e_comm pull origin master
```

## 本次 MX / UK 相关路径（2026-06）

| 区域 | 主要路径 |
|------|----------|
| MX 妙手 | `modules/miaoshou/mx_*.py`，`scripts/migrate_mx_group.py`，`scripts/feishu_mx_dispatch.py`，`web/mx.html` |
| UK 妙手 | `modules/miaoshou/uk_*.py`，`modules/pricing/uk_*.py`，`config/uk_4pl_pricing.json`，`config/uk_commission_rates.json`，`scripts/uk_*.py`，`web/uk.html` |
| 多 SKU 整组 | `modules/catalog/tk_sku_groups.py`，`modules/miaoshou/migrate_dispatch.py` |
| Agent 说明 | `AGENTS.md`，`.cursor/rules/uk-4pl-pricing.mdc` |

## 禁止提交

- `config/settings.json`、`config/*.local.json`
- `tiktok_tokens*.json`、`*.db`
- `data/` 下除 `data/weight_overrides.json` 外的运行时文件
- `backups/`、`.wheels/`、导出 xlsx/csv

## 提交并推送

```powershell
cd C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm

git status
git add AGENTS.md docs/GIT_SYNC_MX_UK.md modules/miaoshou/ modules/pricing/ modules/catalog/tk_sku_groups.py scripts/feishu_*_dispatch.py scripts/orbit_* scripts/migrate_mx_group.py scripts/uk_*.py web/mx.html web/uk.html config/uk_*.json .cursor/rules/ .gitignore

git -c user.name="Kylebit" -c user.email="Kylebit@users.noreply.github.com" commit -m "feat(mx,uk): sync migration pipeline, web approval, and 4PL pricing."

git push origin master
```

推送后在飞书战情室广播（可选）：

```text
@OrbitHive broadcast 已提交/合并 tiktok_e_comm（MX+UK），请相关 agent 拉取最新版本。
```

## 分支说明

- 本地默认分支：`master`
- 远程另有 `main`（历史）；**MX/UK 日常 push 到 `master`**
- 若 `git status` 显示 ahead of origin/master，先 push 再开新任务

## 冲突时

1. `git pull origin master`
2. 解决冲突文件后 `git add` + `git commit`
3. `git push origin master`
4. 飞书说明冲突文件与处理方式
