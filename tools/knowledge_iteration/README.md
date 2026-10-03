# 知识迭代入口

现知识页面的“知识迭代”区展示索引摘要与未评审候选。四类工具、13项介绍和技术调用保持原样。摘要是一次采集时点，不是实时 Vault 监控；日期分级不证明事实正确。候选不能授予运行权限。

## 已有工具来源

`readonly_index_v2.py` 原字节复用已独立验收的 `D:/OrbitEvidence/knowledge-closure-20260908/knowledge_closure.py`，SHA-256 `cf2089f9a7bf1e4459d53033acd5283a5a92565bb85b0eabd8e559e3baf2f9bd`。验收范围为受控日期索引、源字节核对和候选边界，见同目录 `INDEPENDENT_V2_ACCEPT.md`。未重新发明日期分类算法。

该工具的 `catalog-candidate` 命令保留历史目录案例的固定文案/时间/版本，只能复现那个案例，不能当作任何新案例的通用生成器。新案例应按现有 `docs/knowledge/` 合同整理独立候选，不直接晋升。

## 维护

在已核验工程根使用项目 Python；以下路径由维护者明确提供：

```text
python -X utf8 tools/knowledge_iteration/readonly_index_v2.py index --vault <真实Vault根> --output <Vault外独立目录>/vault-index.json
python -X utf8 tools/knowledge_iteration/readonly_index_v2.py search --index <索引路径> --query <主题> --limit 6
python -X utf8 tools/knowledge_iteration/build_projection.py --index <索引路径> --candidate <候选manifest路径> --guides web/static/knowledge_guides.json
```

需要复现历史审计日时给 index 加 `--as-of YYYY-MM-DD`；日常刷新省略该参数，使用上海当前日。生成文件必须留在 Vault 外，不能将输出路径指向原笔记。这里只使用 index/search；不执行付费API或平台调用。

投影保留总数、分类、时效计数、来源路径/摘要和候选身份，不发布真实 Vault 正文、原始来源URL或凭据。只有候选字节匹配时才摘录其“可复用经验候选”段，最多4条，每条500字符。对索引每条源笔记重新核hash后才记录一致/变化/缺失计数；候选及四份验收来源也重算hash。`build_projection.py` 保留原13项指南，只更新 `iteration` 字段。空索引、无效索引和不可核对的候选分别显示，不默认为当前有效。

本轮提交的汇总来自2026-09-08历史审计，源字节核对时间单独记录。仓库演练未来部署时必须刷新来源或保留“历史截点”标识。页面只复制命令，不执行生成、写Vault或批准。

## 正式知识快照

投影输出强制限定为此工具所在工程的 `web/static/knowledge_guides.json`，不接受任意文件目标、父路径跳转、符号链接或重解析点。检查在解析投影前完成，索引缺失/无效不能解除边界；即使schema无效，输入声明的Vault根仍单独检查。拒绝时不改输出文件。

阅读 `docs/knowledge/README.md`、`docs/knowledge/USAGE.md`，按现有评审清单和 `knowledge_version` 进行 export-preview/parity。不能以hash一致代替批准，不能用候选更新时间替代业务观察时间。任何真实业务动作仍回到相应数据库/官方接口核验。
