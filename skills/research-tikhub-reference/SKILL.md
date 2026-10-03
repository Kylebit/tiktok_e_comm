---
name: research-tikhub-reference
description: Use the retained TikHub query and sample-analysis tools for bounded TikTok product/video research. Queries may be billable; results are reference evidence and never publication or media reuse authority.
---

# TikHub 参考调研

先选择稳定 runtime 和非秘密项目 profile；环境键名保留 `TikHub`，不读取 Windows 注册表或个人旧配置。最短参数与恢复办法见 [query-contract.md](references/query-contract.md)。

用 `orbit_tools.py ... preview tikhub query --payload request.json` 查看两次固定查询，再复用覆盖该 query/region/count 与两次请求预算的既有授权。`execute` 前持久化 attempted；HTTP 400、超时、解析错误与中断均不自动重发。缺授权才向用户说明具体缺口，不能将同范围技术参数变成重复审批。

原始响应、请求参数、采集时间与 manifest 必须留在所选产物根。样本销量不是市场总量；缺数据保持未知。不得据 TikHub 视频生成转载、发布或素材许可结论。
