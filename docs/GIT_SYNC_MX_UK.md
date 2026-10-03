# Git 操作导航（含 MX / UK）

唯一规则见[线程治理的 Git 段](THREAD_OPERATING_MODEL.md#git)。本页不授予 pull、push、合并或更改身份权限；旧本机路径、master 默认和“ahead 先 push”已退出当前操作说明。

1. 在当前工单的实际根核验 top-level、branch、HEAD、status-uall 与 remote。
2. 按工单 base 建立隔离工作树；精确审查本次 diff 与文件清单。
3. 暂存清单内路径并核对 staged diff，使用既有身份形成聚焦提交。
4. 回传精确提交、验证与未解项；集成/推送按其明确授权单独执行。

WIP、生成产物、秘密和工作树清理均按同一[Git 规则](THREAD_OPERATING_MODEL.md#git)处理。历史 MX/UK 路径与参数见[历史参考](LEGACY_PLATFORM_REFERENCE.md)，不能执行历史批量命令来同步当前树。
