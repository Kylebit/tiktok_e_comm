# 商品发布知识：来源、版本与只读回放

当前实际入口是 `scripts/sync_product_publication_knowledge.py`，真实包导入为 `modules.product_agent.knowledge`。`modules/product_agent/__init__.py` 仅导出知识类型和函数；旧 shadow/model runtime、prepublication 和 store 未整合，知识可用不代表整个 product_agent 可执行。当前依赖为标准库与既有纯路径 helper `shared_platform/capability_runtime.py`，未使用 checkpoint 锁或业务数据库。

最低 Python 要求为 3.11（本包原子写清理错误使用 `BaseException.add_note`）；实际验证为 Windows、Python 3.12.8、pytest 8.4.2。其他 Python/文件系统未验证。快照使用同目录临时文件、fsync 和不覆盖目标的原子 hard-link 发布；目标文件系统必须支持此操作，不满足时返回真实错误，不回退为覆盖旧快照。

## 与候选原生任务服务的边界（2026-10-03）

候选服务的显式新 publication/profit 接续、原 typed R1/R2 生产器及 COMMON→唯一市场终审，仍由各自 task/source/lease、冻结来源和领域回执证明。知识 profile、清单或笔记不生成新 task grant，不接管同月复用旧利润任务，不补缺失 paid-history baseline、预算或官方店铺身份。旧 usage provenance 兼容不等于已拥有原付费 request 的恢复权限。

本知识 CLI、原 K00 验证与下文固定 portable `/6` 的27文件范围保持原合同；27项不代表完整服务、agent 可执行文件、图片脚本、生命周期或页面已全部安装。候选源码与个人 installed Skill 的英文 MD 可以相同而脚本不同，服务应绑定实际 profile.root 的已验源及闭包，不能借个人旧脚本证明运行版本。更新本说明后须由原 builder 重新派生对应 metadata；该文件的修改不部署服务、不触碰真实 Vault，也不替换原任务固定 knowledge_version。

## 最短路径

`R` 为当前代码根，`P` 为明确的项目根。没有显式 Vault/profile 时，默认预览直接返回定位错误，不访问 APPDATA 最近打开项、原 Vault 或个人配置。示例命令不会授予评审、付费或发布权限。

```text
python R/scripts/sync_product_publication_knowledge.py --help
python R/scripts/sync_product_publication_knowledge.py inspect --runtime-root R --project-root P --vault vault --source-id source-a
python R/scripts/sync_product_publication_knowledge.py --runtime-root R --project-root P --profile knowledge-profile.json --output snapshots/version-a.json
python R/scripts/sync_product_publication_knowledge.py --runtime-root R --project-root P --profile knowledge-profile.json --output snapshots/version-a.json --write
python R/scripts/sync_product_publication_knowledge.py parity --runtime-root R --project-root P --profile knowledge-profile.json --snapshot snapshots/version-a.json
python R/scripts/sync_product_publication_knowledge.py inspect --runtime-root R --project-root P --snapshot snapshots/version-a.json --expected-version sha256:EXPECTED --query keyword
```

无清单的 inspect 只报告参考文档、当前字节摘要和精确缺口，返回非 0 的未就绪结果；不会因 `status:reviewed/active/draft`、目录位置、历史批准或 CURRENT 字样自动纳入运行。原有自由笔记不必批量改 frontmatter。已有评审明确批准某份 draft 的精确字节时，可由上层清单绑定此既有决定；元数据 draft 本身既不批准也不否定上层有效评审。

## 两份明确分工的输入

知识 profile schema 为 `product-publication-knowledge-profile/v1`，与 T00 的 provider profile 不同；未改变 `orbit-tool-profile/v1` 或其目录/manifest。profile 只接受下列字段，无凭据、店铺、付费参数或默认用户身份：

```json
{
  "schema_version": "product-publication-knowledge-profile/v1",
  "project_id": "project-a",
  "vault_root": "vault",
  "source_id": "source-a",
  "section": "99-后台/01-系统规则/商品发布Agent",
  "review_manifest": "review.json",
  "expected_review_digest": "sha256:PIN_FROM_EXISTING_REVIEW",
  "expected_knowledge_version": "sha256:OPTIONAL_EXISTING_TASK_VERSION"
}
```

占位 digest 必须替换为完整 64 位小写 SHA-256；新建快照可省略 `expected_knowledge_version`，已有任务保留自己的版本。`review_manifest`、profile、snapshot 和 output 都位于显式 `P` 内；相对 Vault 路径也相对 `P`，绝对 Vault 路径必须显式提供并通过祖先/链接/reparse 检查。`--vault` 优先于 profile，但仍要匹配原清单的完整来源。输出不能位于源 Vault，不能覆盖不同旧版本。

清单 schema 为 `product-publication-knowledge-review/v1`，完整字段如下：

| 字段 | 含义 |
|---|---|
| source | `source_id`、规范化绝对 `vault_root`、精确相对 `section` |
| review_id / review_scope / reviewed_at | 上层已有评审身份、范围、带时区日期 |
| instruction_ref | 上层证据引用 `audit://...`；离线测试用 `fixture://...` |
| documents | 有界文档数组；每份有 `document_id`、相对 path、原字节 source_digest、usage、source_date、supersedes、report_refs |
| usage | `runtime_rule` 为已核准运行文档；`reference` 只作为参考，不进入运行快照 |
| manifest_digest | 其余完整字段规范 JSON 的 SHA-256；profile 另行绑定此预期值 |

`version_digest` 使用 UTF-8、排序键、无额外空白 JSON；技术序列化可复用函数 `knowledge.version_digest(unsigned_manifest)`。这不是审批工具，也不自行证明用户/评审者身份。清单摘要必须来自上层已核验的同范围输入，不能把修改后的 hash 自动标为批准；已有有效评审不需要因本格式再次提问。

`reference` 文档可用显式 `--reference` 导出独立参考快照。该快照含 `knowledge_mode: reference`，其版本摘要与运行快照分开；默认运行模式不能加载它，参考模式读取时必须给任务固定的完整 `--expected-version`。参考检索结果只标为 `reference`，不进入运行规则集合。来源时点补正应写入 `review_scope` 并链接具名审核报告；CLI 的 provenance 会显示这段范围说明。首次创建参考快照可先预览取得版本，再用 `--write` 写到项目根内的新路径：

```text
python R/scripts/sync_product_publication_knowledge.py export-preview --runtime-root R --project-root P --profile knowledge-profile.json --reference --output snapshots/reference-v1.json
python R/scripts/sync_product_publication_knowledge.py export-preview --runtime-root R --project-root P --profile knowledge-profile.json --reference --output snapshots/reference-v1.json --write
python R/scripts/sync_product_publication_knowledge.py inspect --runtime-root R --project-root P --profile knowledge-profile.json --reference --snapshot snapshots/reference-v1.json --expected-version sha256:PIN_FROM_EXPORT --query keyword
```

没有 `--reference` 的纯参考清单导出仍返回 `no approved runtime documents`；缺固定版本、版本不符或用默认运行模式读取参考快照均失败关闭。参考快照只证明审核内容的固定字节和出处，不授予当前任务执行权。

重复文档身份、大小写别名、源路径越界、缺文件、源字节变化或清单与所选来源不符都会给出具体缺口。导入前完整检查受控 section 的实际路径及重定向，再读取有界 Markdown；该 section 中未纳入清单的文档仅在 inspect 中显示参考理由。现有文件/祖先 symlink 与 Windows reparse 均拒绝；这是静态文件边界校验，不声称抵御任意对抗性 TOCTOU。

## 版本、迁移与恢复

v2 的版本覆盖 schema、section、完整 source、完整已核准清单及文档。每份保留原 `source_text`（含 UTF-8 BOM/CRLF）、原字节 digest、解析后的 metadata/content，加载时重算并验证三者相符。运行构造必须接收任务 `expected_version` 或已核准 `expected_review_digest`；仅 sha256 前缀或重算新版本不构成批准。公开 snapshot 属性和检索元数据返回副本，避免调用方修改后仍借原已验版本搜索。

v1 保留原公式：版本只覆盖 `schema_version`、`section`、`documents`；旧导出额外的 `vault_name` 未进入版本，不能用来证明来源。v1 没有原 source_text 和评审范围，不能重建其 frontmatter/原字节；有效历史版本只允许显式只读 replay：

```text
python R/scripts/sync_product_publication_knowledge.py inspect --runtime-root R --project-root P --snapshot legacy.json --expected-version sha256:EXPECTED --replay --query keyword
```

v1 不自动转换为 v2，也不要求修改原 Vault。迁移时保存原 snapshot，按本轮已核验源字节与既有评审清单生成新的 v2 文件；原任务继续绑定原版本。真实运行器的 U01 producer 必须明确消费运行模式对象，不能把 `mode=replay` 的参考检索结果注入运行规则。r4 runtime 曾接收 KnowledgeBase 并比较 run.knowledge_version；该运行器未由 K00 恢复。

同路径已存在且内容相同，复跑为 0 新写；不同内容拒绝并要求新的版本路径。发布前 fsync 失败保留主错，清理次错附注；文件已经原子发布而清理失败时 CLI 报 `PUBLISHED` / 1 写，复跑校验旧文件后 0 新写。发布结果无法判断时保留 `UNKNOWN`，不声称未写、不删除旧证据。当前测试包含显式 fsync/cleanup 故障注入及 os.link 前注入竞争文件，未声称真实进程 kill 或两个真实进程竞争已覆盖。

## 当前源码、portable 与页面边界

inspect 给出来源、source_date、knowledge_version/provenance、reviewed_at/review_scope、纳入/排除理由、supersedes、original_ref 和 report_refs。`current_execution_authority` 与 `fresh_business_facts_verified` 固定为 false：这些是知识证据，不是实时库存/价格/预算或当前发布许可。动态事实仍由业务数据库、正式回执及相应领域消费者负责。

截至 K00-B 基线 `fe0eeb1fa4b786f29736fa6f44a91a546c2d046d`，实际范围如下；这是源码/包核验，不是当前 APP 或真实 Vault 验收：

| 层 | 已有入口 | 尚不能据此声称 |
| --- | --- | --- |
| 完整源码 | 本 CLI、`modules.product_agent.knowledge`、只含知识导出的包 init | 旧 shadow/runtime/store 已恢复，或完整发布运行器已消费知识 |
| portable /6 | 固定白名单包含上述知识模块、CLI、本文；catalog 的 `publication-knowledge` 为 `PORTABLE_REVIEWED_SNAPSHOT_ENTRY` | 包含 Vault、真实快照、个人配置、完整 product_agent 或 APP |
| 前端方法卡 | 完整源码 `shared_platform/orbit_registry.py` 的 `navigation_payload` 输出知识卡；`/api/orbit/navigation` 提供投影，`web/static/orbit_product.js` 展示并复制方法 | 卡片执行了 CLI、已读取 Vault，或业务运行器自动采用新版本 |
| checkpoint | 当前组合已有 `58a81e9ded5264fc16d897ef6a023ddbbfb2b2e9` 的 S02-D 锁修复 | 知识导出使用该锁；知识模块仍用自己的不覆盖 hard-link 发布 |

知识进入 portable 与方法卡的来源为 `723476b6bdbad52f408e6ec97ccc60d8aeaaa04f`；后续 T00/S04/R00 修正沿用既有入口。旧 K00-A 回执中“未纳入 T00/未接 UI/未合锁”仅描述当时基线，不能继续作为当前待办。完整源码另有 `docs/knowledge/USAGE.md` 的 Obsidian 场景与交接指南；该指南不随固定 portable 白名单分发。

卡片两行分别是无输出文件的 preview，以及对已存在 snapshot 的固定版本 inspect；第二行不会自动导出第一行的结果。首次使用先按本文显式 `--output ... --write` 创建新快照，再把准确文件名和完整版本填入 inspect。方法复制不会执行。真实 Vault 写回、经营实验、自动规则晋升、完整运行器消费与最终 APP/U01 集成仍需各自后续工作包。
