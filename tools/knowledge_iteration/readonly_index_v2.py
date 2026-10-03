from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA = "orbit-knowledge-readonly-index/v2"
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_TZINFO = timezone(timedelta(hours=8), name="Asia/Shanghai")
DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-/.](0[1-9]|1[0-2])[-/.]([0-2]\d|3[01])(?!\d)")
SHA_RE = re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])", re.I)
SOURCE_SHA256_RE = re.compile(r"(?<![0-9a-f])sha256:[0-9a-f]{64}(?![0-9a-f])", re.I)
PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/][^\s`|<>]+|(?:https?|audit|fixture)://[^\s)`|<>]+)")
SOURCE_WORDS = ("来源", "source", "报告", "report", "commit", "回执", "evidence", "pulled_at", "observed_at")
DECISION_WORDS = ("决定", "决策", "批准", "拒绝", "授权", "选择", "decision")
INCIDENT_WORDS = ("事故", "故障", "失败", "根因", "修复", "回归", "复盘", "incident")
OBSERVATION_KEYS = {
    "observed_at", "observed_at_utc", "pulled_at", "pulled_at_utc",
    "source_date", "report_date", "data_as_of", "as_of",
    "数据截至", "数据截止", "截止时间", "统计截至", "报告日期", "观察日期",
    "抓取时间", "拉取时间", "报告周期", "统计周期",
}
MAINTENANCE_KEYS = {"document_updated_at", "updated_at", "modified_at", "generated_at", "created_at", "文档更新", "更新时间"}
PLAN_KEYS = {"planned_at", "plan_date", "next_review_at", "due_date", "target_date", "计划日期", "下次复核", "目标日期"}
OBSERVATION_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?([^:：]{1,40})\s*[:：]\s*(.+)$", re.I)


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def norm_DATE(value: str) -> date | None:
    try:
        return date.fromisoformat(value.replace("/", "-").replace(".", "-"))
    except ValueError:
        return None


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    normalized = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        return {}, normalized
    end = normalized.find("\n---\n", 4)
    if end < 0:
        return {}, normalized
    meta: dict[str, str] = {}
    for line in normalized[4:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip('"\'')
    return meta, normalized[end + 5 :]


def title_of(path: Path, meta: dict[str, str], body: str) -> str:
    if meta.get("title"):
        return meta["title"]
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def note_kind(rel: str, text: str) -> str:
    posix = rel.replace("\\", "/")
    if posix.startswith("90-"):
        return "historical_reference"
    if posix.startswith("99-后台/01-系统规则"):
        return "operating_rule"
    if posix.startswith(("01-", "02-", "03-", "10-")):
        return "business_fact_or_analysis"
    if any(word in text.casefold() for word in INCIDENT_WORDS):
        return "incident_or_learning"
    if any(word in text.casefold() for word in DECISION_WORDS):
        return "decision_record"
    return "supporting_note"


def date_scan(text: str) -> dict[str, list[str]]:
    values: set[str] = set()
    invalid: set[str] = set()
    for year, month, day in DATE_RE.findall(text):
        value = f"{year}-{month}-{day}"
        try:
            datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            invalid.add(value)
            continue
        values.add(value)
    return {"valid": sorted(values), "invalid": sorted(invalid)}


def dates_in(text: str) -> list[str]:
    return date_scan(text)["valid"]


def normalized_observation_key(value: str) -> str:
    return value.strip().casefold().replace("-", "_")


def is_observation_key(value: str) -> bool:
    key = normalized_observation_key(value)
    if key in OBSERVATION_KEYS:
        return True
    return key.endswith(("_observed_at", "_observed_at_utc", "_pulled_at", "_pulled_at_utc"))


def observation_date_facts(meta: dict[str, str], body: str, as_of: date) -> dict[str, Any]:
    occurrences: list[dict[str, Any]] = []
    for key, value in meta.items():
        if is_observation_key(key):
            scan = date_scan(value)
            occurrences.append({"location": "frontmatter", "field": key, "dates": scan["valid"], "invalid_dates": scan["invalid"]})
    for line_number, line in enumerate(body.splitlines(), start=1):
        match = OBSERVATION_LINE_RE.match(line)
        if match and is_observation_key(match.group(1)):
            scan = date_scan(match.group(2))
            occurrences.append({
                "location": f"body_line:{line_number}",
                "field": match.group(1).strip(),
                "dates": scan["valid"],
                "invalid_dates": scan["invalid"],
            })
    recognized = sorted({value for item in occurrences for value in item["dates"]})
    return {
        "recognized_observation_dates": recognized,
        "valid_observation_dates": [value for value in recognized if date.fromisoformat(value) <= as_of],
        "future_observation_dates": [value for value in recognized if date.fromisoformat(value) > as_of],
        "invalid_observation_date_tokens": sorted({value for item in occurrences for value in item["invalid_dates"]}),
        "occurrences": occurrences,
    }


def contextual_date_facts(meta: dict[str, str], body: str) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {"maintenance": [], "plan": []}
    for key, value in meta.items():
        normalized = normalized_observation_key(key)
        bucket = "maintenance" if normalized in MAINTENANCE_KEYS else "plan" if normalized in PLAN_KEYS else None
        if bucket:
            scan = date_scan(value)
            buckets[bucket].append({"location": "frontmatter", "field": key, **scan})
    for line_number, line in enumerate(body.splitlines(), start=1):
        match = OBSERVATION_LINE_RE.match(line)
        if not match:
            continue
        normalized = normalized_observation_key(match.group(1))
        bucket = "maintenance" if normalized in MAINTENANCE_KEYS else "plan" if normalized in PLAN_KEYS else None
        if bucket:
            scan = date_scan(match.group(2))
            buckets[bucket].append({"location": f"body_line:{line_number}", "field": match.group(1).strip(), **scan})
    return {
        "maintenance_date_occurrences": buckets["maintenance"],
        "plan_date_occurrences": buckets["plan"],
    }


def freshness(kind: str, observation: dict[str, Any], refs: list[str], text: str, as_of: date) -> tuple[str, str]:
    dates = observation["valid_observation_dates"]
    future_dates = observation["future_observation_dates"]
    newest = max((date.fromisoformat(value) for value in dates), default=None)
    if kind == "historical_reference":
        return "HISTORICAL_REFERENCE", "历史资料只作定向参考，不作为当前事实"
    if kind == "operating_rule":
        pinned = bool(SOURCE_SHA256_RE.search(text)) and bool(refs)
        if pinned:
            return "PINNED_REFERENCE_NEEDS_SCOPE_CHECK", "存在版本/来源引用；使用前仍核适用范围和替代关系"
        return "RULE_NEEDS_REVALIDATION", "规则未同时绑定完整版本与来源，不能自动晋升为运行规则"
    if kind == "business_fact_or_analysis":
        if future_dates:
            return "INVALID_FUTURE_OBSERVED_AT", "识别到未来观察日期，不得据此标记当前"
        if newest is None:
            return "UNKNOWN_OBSERVED_AT", "未在受控观察字段中提取到日期；任意正文日期与mtime均不代用"
        if len(dates) > 1:
            return "MULTIPLE_OBSERVATION_DATES_NEEDS_SCOPE", "存在多个不同观察日期，可能是区间或多源比较；不自动选最新日期作权威"
        age = (as_of - newest).days
        if age <= 2:
            return "CURRENT_BY_DATE_ONLY", f"唯一受控观察日期距审计日{age}天；只是时间风险提示，不证明事实正确"
        if age <= 7:
            return "RECENT_BY_DATE_ONLY", f"唯一受控观察日期距审计日{age}天；仅作时间风险提示，动作前应刷新"
        return "STALE_FOR_CURRENT_DECISION", f"唯一受控观察日期距审计日{age}天，只保留当时截点"
    if newest and refs:
        return "DATED_REFERENCE", "有日期和来源引用；按引用范围使用"
    return "UNVERIFIED_REFERENCE", "缺少可机器核验的日期或来源绑定"


def source_refs(text: str) -> list[str]:
    refs = list(dict.fromkeys(match.group(0).rstrip(".,;，。；") for match in PATH_RE.finditer(text)))
    return refs[:20]


def excerpt(body: str, length: int = 700) -> str:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    return "\n".join(lines)[:length]


def terms(value: str) -> list[str]:
    lowered = value.casefold()
    ascii_terms = re.findall(r"[a-z0-9][a-z0-9_.-]+", lowered)
    chinese_runs = re.findall(r"[\u3400-\u9fff]+", lowered)
    chinese_terms: list[str] = []
    for run in chinese_runs:
        chinese_terms.append(run)
        chinese_terms.extend(run[index : index + 2] for index in range(max(0, len(run) - 1)))
    return list(dict.fromkeys(ascii_terms + chinese_terms))


def build_index(vault: Path, output: Path, as_of: date, as_of_source: str) -> dict[str, Any]:
    vault = vault.resolve()
    rows: list[dict[str, Any]] = []
    for path in sorted(vault.rglob("*.md"), key=lambda value: value.as_posix().casefold()):
        if ".obsidian" in path.parts:
            continue
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig", errors="replace")
        meta, body = parse_frontmatter(text)
        rel = path.relative_to(vault).as_posix()
        refs = source_refs(text)
        full_scan = date_scan(text)
        mentioned_dates = full_scan["valid"]
        observation = observation_date_facts(meta, body, as_of)
        contextual = contextual_date_facts(meta, body)
        kind = note_kind(rel, text)
        freshness, reason = freshness_of(kind, observation, refs, text, as_of)
        headings = [line.lstrip("# ").strip() for line in body.splitlines() if line.startswith("#")][:12]
        cited_source_digests = sorted(set(SOURCE_SHA256_RE.findall(text)))
        rows.append({
            "path": rel,
            "title": title_of(path, meta, body),
            "kind": kind,
            "freshness": freshness,
            "freshness_reason": reason,
            "effective_observation_date": observation["valid_observation_dates"][0] if len(observation["valid_observation_dates"]) == 1 and not observation["future_observation_dates"] else None,
            "recognized_observation_dates": observation["recognized_observation_dates"],
            "future_observation_dates": observation["future_observation_dates"],
            "invalid_observation_date_tokens": observation["invalid_observation_date_tokens"],
            "observation_date_occurrences": observation["occurrences"],
            "all_mentioned_dates": mentioned_dates,
            "invalid_mentioned_date_tokens": full_scan["invalid"],
            **contextual,
            "source_refs": refs,
            "source_ref_count": len(refs),
            "cited_source_digests": cited_source_digests,
            "source_hash_binding": "CITED_SOURCE_DIGEST_PRESENT_NOT_READBACK_VERIFIED" if cited_source_digests else "NOTE_BYTES_BOUND_ONLY",
            "has_decision_language": any(word in text.casefold() for word in DECISION_WORDS),
            "has_incident_language": any(word in text.casefold() for word in INCIDENT_WORDS),
            "frontmatter_status": meta.get("status"),
            "headings": headings,
            "excerpt": excerpt(body),
            "content_digest": sha256_bytes(raw),
            "size_bytes": len(raw),
            "file_mtime_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        })
    counts = {
        "notes": len(rows),
        "by_kind": dict(sorted(Counter(row["kind"] for row in rows).items())),
        "by_freshness": dict(sorted(Counter(row["freshness"] for row in rows).items())),
        "with_source_refs": sum(bool(row["source_refs"]) for row in rows),
        "with_mentioned_date": sum(bool(row["all_mentioned_dates"]) for row in rows),
        "with_observation_date": sum(bool(row["recognized_observation_dates"]) for row in rows),
        "with_future_observation_date": sum(bool(row["future_observation_dates"]) for row in rows),
        "with_invalid_date_warning": sum(bool(row["invalid_mentioned_date_tokens"]) for row in rows),
        "with_multiple_observation_dates": sum(
            len([value for value in row["recognized_observation_dates"] if value not in row["future_observation_dates"]]) > 1
            for row in rows
        ),
        "decision_language": sum(row["has_decision_language"] for row in rows),
        "incident_language": sum(row["has_incident_language"] for row in rows),
    }
    payload = {
        "schema_version": SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "audit_date": as_of.isoformat(),
        "audit_date_source": as_of_source,
        "audit_timezone": DEFAULT_TIMEZONE,
        "vault_root": str(vault),
        "read_only": True,
        "classification_policy": {
            "business_current_days": 2,
            "business_recent_days": 7,
            "mtime_is_not_business_freshness": True,
            "arbitrary_body_dates_are_not_observation_dates": True,
            "recognized_observation_fields_only": sorted(OBSERVATION_KEYS),
            "multiple_observation_dates_do_not_auto_select_latest": True,
            "future_observation_dates_are_invalid_for_currentness": True,
            "date_window_is_heuristic_not_fact_correctness": True,
            "static_current_labels_are_not_trusted": True,
            "knowledge_is_not_execution_authority": True,
            "note_digest_binds_extraction_to_note_bytes_only": True,
            "cited_source_digest_presence_is_not_provider_readback": True,
        },
        "counts": counts,
        "documents": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = output.with_suffix(".csv")
    fields = ["path", "title", "kind", "freshness", "effective_observation_date", "source_ref_count", "source_hash_binding", "has_decision_language", "has_incident_language", "frontmatter_status", "content_digest", "size_bytes", "file_mtime_utc"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return payload


def freshness_of(kind: str, observation: dict[str, Any], refs: list[str], text: str, as_of: date) -> tuple[str, str]:
    return freshness(kind, observation, refs, text, as_of)


def search(index_path: Path, query: str, limit: int) -> dict[str, Any]:
    index = json.loads(index_path.read_text(encoding="utf-8"))
    vault = Path(index["vault_root"])
    query_terms = terms(query)
    results = []
    drift = []
    for row in index["documents"]:
        path = vault / PurePosixPath(row["path"])
        if not path.exists():
            drift.append({"path": row["path"], "state": "MISSING"})
            continue
        raw = path.read_bytes()
        digest = sha256_bytes(raw)
        if digest != row["content_digest"]:
            drift.append({"path": row["path"], "state": "SOURCE_DIGEST_CHANGED", "indexed": row["content_digest"], "current": digest})
            continue
        haystack = (row["title"] + "\n" + row["path"] + "\n" + row["excerpt"] + "\n" + json.dumps(row["headings"], ensure_ascii=False)).casefold()
        score = sum(5 if term in row["title"].casefold() else 3 if term in row["path"].casefold() else 1 for term in query_terms if term in haystack)
        if score:
            results.append({
                "path": row["path"],
                "title": row["title"],
                "score": score,
                "kind": row["kind"],
                "freshness": row["freshness"],
                "effective_observation_date": row["effective_observation_date"],
                "recognized_observation_dates": row["recognized_observation_dates"],
                "future_observation_dates": row["future_observation_dates"],
                "invalid_observation_date_tokens": row["invalid_observation_date_tokens"],
                "maintenance_date_occurrences": row["maintenance_date_occurrences"],
                "plan_date_occurrences": row["plan_date_occurrences"],
                "content_digest": row["content_digest"],
                "source_hash_binding": row["source_hash_binding"],
                "excerpt": row["excerpt"],
            })
    results.sort(key=lambda row: (-row["score"], row["path"].casefold()))
    return {
        "state": "READY" if not drift else "INDEX_DRIFT_DETECTED",
        "query": query,
        "index": str(index_path.resolve()),
        "source_digest_verified_before_return": True,
        "results": results[:limit],
        "drift": drift,
        "current_execution_authority": False,
    }


def build_catalog_candidate(evidence_root: Path, output_root: Path) -> dict[str, Any]:
    source_names = ["ROOT_DEPLOYMENT_VERIFICATION.json", "REVIEW_ACCEPTED.json", "HANDOFF.md", "COVERAGE.json"]
    sources = []
    for name in source_names:
        path = evidence_root / name
        raw = path.read_bytes()
        sources.append({"path": str(path.resolve()), "digest": sha256_bytes(raw), "size_bytes": len(raw)})
    verify = json.loads((evidence_root / "ROOT_DEPLOYMENT_VERIFICATION.json").read_text(encoding="utf-8"))
    coverage = json.loads((evidence_root / "COVERAGE.json").read_text(encoding="utf-8"))
    accepted = json.loads((evidence_root / "REVIEW_ACCEPTED.json").read_text(encoding="utf-8"))
    candidate_vault = output_root / "candidate-vault"
    section = candidate_vault / "知识候选"
    note = section / "商品目录-内部SKU-0001-验收经验候选.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    content = f'''---
title: 商品目录内部SKU 0001 验收经验候选
status: candidate_not_reviewed
source_date: 2026-09-07
scope: catalog/internal-sku/read-only-review-copy
current_execution_authority: false
fresh_business_facts_verified: false
---

# 商品目录内部SKU 0001 验收经验候选

## 适用范围

仅适用于商品目录“一个内部SKU一行”的审核副本与显示/身份合并逻辑；不授权修改正式数据库、成本、平台商品或发布流程。

## 已证实观察

- 固定验收提交：`{accepted['head']}`。
- 1,729 个来源身份被唯一分配到 314 行，其中 313 个内部SKU，另有1条缺SKU记录。
- SKU `0001` 在实际页面检索后仅显示1行、1个成本输入框，成本值为 `13.5`，至少一张已登记原图成功解码，图片自然宽度为 {verify['root_iab']['image_natural_width']}。
- 31项缺成本和3组成本冲突均保留，不以默认值或任意选择覆盖。
- 验收期间数据库快照未改变，平台API调用为0；当前成本编辑只写审核副本。

## 可复用经验候选

1. 内部SKU可以作为跨国家/平台目录聚合键，但平台商品身份、国家、店铺和变体ID必须保留为成员明细，不能合并丢失。
2. 成本共享只能在已核验为同一内部SKU的成员间发生；缺成本继续为空，冲突必须显式阻断人工选择。
3. 图片应从同一内部SKU的已登记候选中回退；一张失败不等于整个SKU无图，但验收必须读取真实解码状态。
4. 页面通过只证明有界审核副本；正式数据切换、成本写回和平台操作需要各自恢复点、授权与官方回读。

## 尚未证明

- 图片只做了样本验证，不是全部URL全验。
- 服务曾退出，原因未知；不能据此宣称永久运行稳定。
- 真实成本写入、正式数据库迁移、平台写入均未执行。
- 当前仓库HEAD为 `74fed8799ef3c6c938a79f84f86decf0aa4b3a66`；本候选所依据的有界验收提交较早，复用前需核相关合同是否被后续提交替代。

## 验证与下一步

- 证据状态：`{verify['status']}`，验收时间 `{verify['verified_at']}`。
- 来源数据库摘要：`{coverage['database_sha256']}`。
- 进入正式运行知识前，必须由上层对本候选精确字节、适用范围和 `supersedes` 关系生成已审清单；仅修改hash不构成批准。
- 新agent先按候选检索命令读取，再回到固定提交、当前HEAD和证据文件核验；本笔记本身不授予写入权限。
'''
    note.write_text(content, encoding="utf-8")
    manifest = {
        "schema_version": "orbit-knowledge-candidate-manifest/v1",
        "status": "CANDIDATE_NOT_REVIEWED",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_vault": str(candidate_vault.resolve()),
        "section": "知识候选",
        "candidate_path": note.relative_to(candidate_vault).as_posix(),
        "candidate_digest": sha256_bytes(note.read_bytes()),
        "source_case": "catalog-internal-sku-0001-bounded-acceptance",
        "source_observed_at": verify["verified_at"],
        "source_evidence": sources,
        "verified_head": accepted["head"],
        "current_repo_head_observed": "74fed8799ef3c6c938a79f84f86decf0aa4b3a66",
        "current_execution_authority": False,
        "fresh_business_facts_verified": False,
        "promotion_gate": "REQUIRES_EXACT_REVIEW_MANIFEST_AND_DIGEST",
        "vault_writes": 0,
        "platform_writes": 0,
    }
    (output_root / "candidate-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def write_summary(index: dict[str, Any], output: Path) -> None:
    counts = index["counts"]
    stale = counts["by_freshness"].get("STALE_FOR_CURRENT_DECISION", 0)
    unknown = counts["by_freshness"].get("UNKNOWN_OBSERVED_AT", 0)
    lines = [
        "# 知识库只读入口索引",
        "",
        f"生成时间：{index['generated_at_utc']}；审计日：{index['audit_date']}；日期来源：{index['audit_date_source']}；时区：{index['audit_timezone']}。",
        "",
        "> 日期分级只是时间风险启发式，不是事实正确性判断。只读受控观察字段，不用任意正文日期、mtime或静态 `CURRENT`。任何知识检索都不授予业务写入或平台执行权限。",
        "",
        "## 覆盖",
        "",
        f"- Markdown 笔记：{counts['notes']}。",
        f"- 含来源引用：{counts['with_source_refs']}。",
        f"- 含任意提及日期（不当观察日）：{counts['with_mentioned_date']}。",
        f"- 含受控观察字段：{counts['with_observation_date']}。",
        f"- 含多个观察日期：{counts['with_multiple_observation_dates']}。",
        f"- 含未来观察日期：{counts['with_future_observation_date']}。",
        f"- 含非法日期告警（已跳过，未中止索引）：{counts['with_invalid_date_warning']}。",
        f"- 当前决策已过期的业务事实/分析：{stale}。",
        f"- 无业务观察日期：{unknown}。",
        f"- 含决策语言：{counts['decision_language']}；含事故/修复语言：{counts['incident_language']}。",
        "",
        "## 使用顺序",
        "",
        "1. 用 `vault-index.json` 搜索标题、路径、摘要与标题层级。",
        "2. 搜索返回前会重算源文件 SHA-256；发生漂移就拒绝返回旧索引内容。",
        "3. 动态业务事实只认 `observed_at/pulled_at/报告周期`，过期后保留当时截点，不继续标当前。",
        "4. 运行规则只有绑定精确源字节、评审清单和固定 knowledge_version 后才可进入既有运行快照；候选笔记不自动晋升。",
        "",
        "## 机器输出",
        "",
        "- `vault-index.json`：完整可检索索引与SHA。",
        "- `vault-index.csv`：便于人工筛选的扁平表。",
        "- `candidate-manifest.json`：真实已完成目录案例的未评审候选闭环。",
        "- `validation.json`：索引、候选检索、现有正式CLI拒绝未评审晋升的验证结果。",
    ]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_as_of(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--as-of must be YYYY-MM-DD") from exc


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Read-only Vault index, digest-verified search, and candidate closure.")
    sub = p.add_subparsers(dest="action", required=True)
    index = sub.add_parser("index")
    index.add_argument("--vault", required=True, type=Path)
    index.add_argument("--output", required=True, type=Path)
    index.add_argument("--summary", type=Path)
    index.add_argument("--as-of", type=parse_as_of, help="Freshness audit date (YYYY-MM-DD). Default: current date in Asia/Shanghai.")
    find = sub.add_parser("search")
    find.add_argument("--index", required=True, type=Path)
    find.add_argument("--query", required=True)
    find.add_argument("--limit", type=int, default=6)
    find.add_argument("--output", type=Path)
    case = sub.add_parser("catalog-candidate")
    case.add_argument("--source-evidence", required=True, type=Path)
    case.add_argument("--output-root", required=True, type=Path)
    return p


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    if args.action == "index":
        as_of = args.as_of or datetime.now(DEFAULT_TZINFO).date()
        as_of_source = "CLI_EXPLICIT" if args.as_of else "DEFAULT_CURRENT_ASIA_SHANGHAI"
        payload = build_index(args.vault, args.output, as_of, as_of_source)
        if args.summary:
            write_summary(payload, args.summary)
        result = {"state": "READY", "index": str(args.output.resolve()), "counts": payload["counts"], "vault_writes": 0}
    elif args.action == "search":
        if not 1 <= args.limit <= 20:
            raise SystemExit("limit must be 1..20")
        result = search(args.index, args.query, args.limit)
        if args.output:
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        result = build_catalog_candidate(args.source_evidence, args.output_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("state") != "INDEX_DRIFT_DETECTED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
