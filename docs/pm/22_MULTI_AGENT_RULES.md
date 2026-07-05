# 多Agent协作规则

## 角色与权限矩阵

| 角色 | Agent | 职责 | 可修改代码 | 可commit/push | 可publish |
|------|-------|------|-----------|---------------|-----------|
| 总控 | Codex Main | 建卡、派任务、回填飞书、分析根因 | 否 | 否 | 需用户确认 |
| TK MX/UK | Cursor | TikTok墨西哥/英国站运营功能 | 是 | 是 | 需用户确认 |
| Ozon | Claude Code | Ozon俄罗斯站运营功能 | 是 | 是 | 需用户确认 |
| 子Agent通用 | 各子Agent | 执行具体任务卡 | 是 | 是 | 需用户确认 |

## 任务派发流程

```
总控                        用户                     子Agent
  │                          │                         │
  ├─ 分析问题 → 建飞书卡      │                         │
  ├─ 整理Dispatch Prompt ──→ │                         │
  │                          ├─ 复制prompt发给子Agent ──→ │
  │                          │                         ├─ 读卡、改代码
  │                          │                         ├─ 跑测试
  │                          │                         ├─ commit + push
  │                          │                         └─ 回报结果
  │                          │                         │
  │  ← 接收回报 ──────────────┤  ←──────────────────────┘
  │                          │
  ├─ 回填飞书（测试状态/备注）  │
  └─ 通知用户                  │
```

## Git 纪律（硬性要求）

### ⚠️ 不commit = 未完成

与"不跑测试不算完成"同一级别。没有 commit hash，任务不算交付。

### 子Agent必须做的事

1. **所有修改完成后，必须 `git add` + `git commit`**
   - 格式：`git commit -m "ORB-TASK-XXXX(Bot): 简述做了什么"`
   - 例：`git commit -m "ORB-TASK-0022(Bot): download_image加指数退避重试+降级逻辑"`

2. **必须 `git push origin main`**（直接推main，不走分支+PR）
   - 原因：3个agent独立clone不同时改同一文件，直接push不会冲突

3. **回报时附带 commit hash**
   - 格式：`commit: abc1234 — ORB-TASK-XXXX: 简述`
   - 总控用这个hash验证代码确实入库了

### 历史教训

**2026-07-04 ORB-TASK-0013 回归事故：**
- 子Agent修好了bug，测试通过，回报"已完成"
- 但从未 `git commit`，修改留在 working tree 脏文件里
- 环境重启后修改丢失 → Bug回归 → 用户发0013命令Bot无响应
- **根因：没有Git纪律，没有commit hash验证**

### 总控验收时检查

收到子Agent回报后，总控通过 git log 确认：
- commit hash 存在
- commit message 包含正确的 ORB-TASK-XXXX
- 之后才回填飞书「已完成」

## 状态机门禁

```
待开发 ──→ 开发中 ──→ 开发完成 ──→ 测试通过 ──→ 已完成
                                    │
                                    ├─ 单元测试 ✓
                                    ├─ 回归测试 ✓
                                    └─ commit+push ✓  ← 新增门禁
```

任一门禁未通过，不允许标记「已完成」。

## 防冲突机制

- 总控每次只给一个子Agent派一张卡
- 不同子Agent负责不同模块（TK vs Ozon），天然隔离
- 如果意外冲突，`git pull --rebase` 后重新push
