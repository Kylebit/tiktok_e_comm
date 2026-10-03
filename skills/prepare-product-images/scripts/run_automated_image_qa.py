"""Run one fail-closed Lingshi visual QA pass and persist its bound receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[3]
QA_ASSESSMENT_BINDING_CONTRACT = 'orbit-qa-assessment-paths/v2'
if __name__ == "__main__" and (not (ROOT / '.git').exists() or not all((ROOT / name).is_file() for name in (
    "core/config.py", "modules/sourcing/new_product_workbench.py",
    "shared_platform/publication_rounds.py",
))):
    raise SystemExit("COMPLETE_AGENT_SOURCE_REQUIRED: use the selected repository's scripts/repo_bound_agent_entry.py --profile <absolute-profile> --entry qa")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.sourcing.lingshi_client import LingshiClient  # noqa: E402
from shared_platform.publication_image_qa import (  # noqa: E402
    ASSESSMENT_SCHEMA,
    build_automated_image_qa,
    persist_automated_image_qa,
)
from shared_platform.publication_rounds import canonical_digest  # noqa: E402




def _runtime_root(runtime):
    if runtime is None:
        return ROOT
    from shared_platform.native_parent_images import NativeImageRuntime
    if type(runtime) is not NativeImageRuntime:
        raise ValueError("R2_SERVICE_RUNTIME_REQUIRED")
    return runtime.checked_root()

def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _content_text(response: Mapping[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("Lingshi QA response has no choices")
    message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
    content = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(content, str):
        raise ValueError("Lingshi QA response content is invalid")
    value = content.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return value


def _normalize_visual_checks(value: Any) -> list[dict[str, Any]]:
    """Recover the documented ordered check list when a model omits only its labels."""

    expected = ("FACTUAL_ALIGNMENT", "OCR_LANGUAGE", "DUPLICATION")
    if not isinstance(value, list) or len(value) != len(expected):
        raise ValueError("Lingshi QA checks must contain exactly three rows")
    rows = [dict(row) for row in value if isinstance(row, Mapping)]
    if len(rows) != len(expected):
        raise ValueError("Lingshi QA checks must contain objects")
    supplied_codes = [
        str(row.get("code") or row.get("name") or row.get("type") or "").strip()
        for row in rows
    ]
    if any(supplied_codes):
        if tuple(supplied_codes) != expected:
            raise ValueError("Lingshi QA check identities or order are invalid")
    else:
        for row, code in zip(rows, expected, strict=True):
            row["code"] = code
    if any(str(row.get("status") or "") not in {"PASSED", "FAILED"} for row in rows):
        raise ValueError("Lingshi QA check status is invalid")
    return rows


def _validate_batch_failure_evidence(
    parsed: Mapping[str, Any], *, valid_asset_ids: set[int]
) -> None:
    """Reject self-contradictory model verdicts before they become QA evidence."""

    findings = parsed.get("asset_findings")
    if not isinstance(findings, list):
        raise ValueError("Lingshi QA asset_findings must be an array")
    normalized_findings = [dict(row) for row in findings if isinstance(row, Mapping)]
    if len(normalized_findings) != len(findings):
        raise ValueError("Lingshi QA asset findings must be objects")
    failed_codes = {
        str(row.get("code") or "")
        for row in parsed.get("checks") or ()
        if isinstance(row, Mapping) and row.get("status") == "FAILED"
    }
    for code in failed_codes:
        matching = [row for row in normalized_findings if str(row.get("code") or "") == code]
        if not matching:
            raise ValueError(f"Lingshi QA {code} failure lacks asset evidence")
        for row in matching:
            if int(row.get("qa_asset_id") or 0) not in valid_asset_ids:
                raise ValueError("Lingshi QA finding references an unknown asset")
            if not str(row.get("evidence") or "").strip():
                raise ValueError("Lingshi QA finding lacks defect evidence")
            if code == "OCR_LANGUAGE" and not str(row.get("observed_text") or "").strip():
                raise ValueError("Lingshi QA OCR failure lacks exact observed text")


def _passed_master_qa_digest(
    *, report_dir: Path, generation: Mapping[str, Any], round1: Mapping[str, Any]
) -> str:
    """Return the current or archived PASS receipt bound to the master images."""

    paths = [report_dir / "automated-image-qa.json"]
    paths.extend(sorted((report_dir / "image-qa-attempts").glob("*.json")))
    candidates: list[dict[str, Any]] = []
    expected_digests=sorted(str(row.get('artifact_digest') or '') for row in generation.get('assets') or [])
    for candidate_path in paths:
        try:
            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, TypeError, ValueError):
            continue
        unsigned=dict(candidate) if isinstance(candidate,dict) else {}
        supplied=unsigned.pop('qa_digest',None)
        if (
            isinstance(candidate, dict)
            and candidate.get("status") == "PASSED"
            and int(candidate.get("generated_asset_count") or 0)
            == len(generation.get("assets") or ())
            and int(candidate.get("localized_asset_count") or 0) == 0
            and candidate.get("round1_snapshot_digest") == round1.get("snapshot_digest")
            and candidate.get('artifact_digests')==expected_digests
            and supplied==canonical_digest(unsigned)
        ):
            candidates.append(candidate)
    if not candidates:
        raise ValueError("localized QA requires one passed master-image QA receipt")
    return str(candidates[-1].get("qa_digest") or "")


def _qa_image_reference(row, *, verified_local=False):
    """Send exactly the bytes named by the QA inventory, including local repairs."""
    if not verified_local:
        url = str(row.get("public_url") or "")
        if not url.startswith("https://"):
            raise ValueError("every QA asset requires an HTTPS public_url")
        return url, 0
    import base64
    import hashlib
    from io import BytesIO
    from PIL import Image
    path = Path(str(row.get('artifact_path') or ''))
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError('verified local QA image unavailable or too large')
    raw = path.read_bytes()
    if 'sha256:' + hashlib.sha256(raw).hexdigest() != row.get('artifact_digest'):
        raise ValueError('verified local QA image digest changed')
    with Image.open(BytesIO(raw)) as image:
        mime = {'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}.get(image.format)
        if mime is None:
            raise ValueError('verified local QA image format is unsupported')
        image.verify()
    return 'data:' + mime + ';base64,' + base64.b64encode(raw).decode('ascii'), len(raw)


def _visual_assessment(
    *,
    client: LingshiClient,
    model: str,
    offer_id: str,
    round1: Mapping[str, Any],
    generation: Mapping[str, Any],
    translation: Mapping[str, Any],
    raw_attempt_path: Path | None = None,
    paid_context=None,
    verified_local_assets=False,
) -> dict[str, Any]:
    from shared_platform.publication_paid_requests import require_paid_context
    context = require_paid_context(paid_context)
    if context.offer_id != str(offer_id) or context.round1 != dict(round1):
        raise ValueError('QA context does not bind the current product/facts')
    all_assets = [
        dict(row)
        for document in (generation, translation)
        for row in document.get("assets") or ()
        if isinstance(row, Mapping)
    ]
    localized_assets = [
        dict(row)
        for row in translation.get("assets") or ()
        if isinstance(row, Mapping)
    ]
    assets = localized_assets or all_assets
    digests = sorted(
        str(row.get("artifact_digest") or "")
        for row in all_assets
        if str(row.get("artifact_digest") or "").startswith("sha256:")
    )
    master_qa_digest = ""
    if localized_assets:
        if raw_attempt_path is None:
            raise ValueError("localized QA requires a durable report directory")
        master_qa_digest = _passed_master_qa_digest(
            report_dir=raw_attempt_path.parent,
            generation=generation,
            round1=round1,
        )
    indexed_assets = list(enumerate(assets, start=1))
    frozen = round1.get("fact_snapshot") or {}
    brand_ids = list(dict.fromkeys(str(row.get("brand_id") or "") for row in assets))
    if len(brand_ids) == 2 and len(assets) > 6:
        batches: list[list[tuple[int, dict[str, Any]]]] = []
        for brand_id in brand_ids:
            primary = [item for item in indexed_assets if str(item[1].get("brand_id") or "") == brand_id]
            other = [item for item in indexed_assets if str(item[1].get("brand_id") or "") != brand_id]
            comparison = [item for item in other if str(item[1].get("role") or "") == "cover"][:1]
            comparison += [item for item in other if str(item[1].get("role") or "").endswith("_scene")][:1]
            for offset in range(0, len(primary), 6):
                batches.append(primary[offset : offset + 6] + comparison)
    else:
        batches = [indexed_assets]

    context.planned_requests=len(batches)
    parsed_batches: list[dict[str, Any]] = []
    raw_responses: list[dict[str, Any]] = []
    for batch_number, batch in enumerate(batches, start=1):
        primary_brand_id = str(batch[0][1].get("brand_id") or "") if batch else ""
        inventory = [
            {
                "qa_asset_id": asset_id,
                "sequence": row.get("sequence") or row.get("source_review_number"),
                "brand_id": row.get("brand_id"),
                "role": row.get("role"),
                "locale": row.get("locale"),
                "artifact_digest": row.get("artifact_digest"),
                "reviewed_local_repair": {
                    key: (row.get("local_provenance") or {}).get(key)
                    for key in ("binding_sha256", "master_sha256", "protected_pixels_verified", "footer_text")
                } if (row.get("local_provenance") or {}).get("schema_version") == "adopted-footer-repair/v1" else None,
                "comparison_only": str(row.get("brand_id") or "") != primary_brand_id,
            }
            for asset_id, row in batch
        ]
        facts = {
            "offer_id": offer_id,
            "product": frozen.get("product_facts"),
            "variants": (frozen.get("shared_review_facts") or {}).get("variants"),
            "image_plan": round1.get("image_plan"),
            "asset_inventory": inventory,
        }
        prompt = (
            "你是商品图片自动质检器。只输出 JSON 对象，不输出思维过程。"
            "图片按 asset_inventory 的 qa_asset_id 顺序附加。只评估 comparison_only=false 的主品牌图片；"
            "comparison_only=true 的图片只参与 DUPLICATION 的跨品牌比较，绝不能用于 "
            "FACTUAL_ALIGNMENT 或 OCR_LANGUAGE 判定。逐张核对图片与给定商品事实及角色。"
            "不得改变花纹、材质、尺寸、数量；仅对 locale 非空的本地化图片检查文字语言，"
            "所有可见说明文字必须属于指定 locale，乱码、混合了不应出现的语言或残留母版文字均失败；"
            "locale 为空表示英文母版，英文文字必须判定为允许语言，不得因此判定 OCR_LANGUAGE 失败。"
            "cm、pcs、尺寸数字属于通用规格标记；母版中允许英文产品图文字。"
            "代表图没有可识别差异时失败；两品牌使用同一真实商品本身不算重复。"
            "locale 非空时才检查该 locale 是否存在其他语言残留；同一角色的不同 locale 是预期版本。"
            "FACTUAL_ALIGNMENT 只在图片明确出现与事实冲突的内容时失败；"
            "不得因为图片没有重复展示规格文字而判定 FACTUAL_ALIGNMENT 失败。"
            "DUPLICATION 只在不同角色实质重复，或两品牌代表图缺乏可识别差异时失败。"
            "只有背景、构图、视角和商品布局都近乎相同，才可判定为实质重复；"
            "同一商品、同一四片图案、不同房间或不同视角必须判定为非重复。"
            "只要文字语言分别正确，不得将它们判定为重复图片。"
            "输出纯 JSON：status=PASSED/FAILED；checks 必须且只能按顺序包含"
            " FACTUAL_ALIGNMENT、OCR_LANGUAGE、DUPLICATION，每项含 status 和简短中文 evidence。"
            "另输出 asset_findings 数组；只列失败图片，每项必须使用英文键 qa_asset_id、code、evidence；"
            "OCR_LANGUAGE 失败项还必须使用英文键 observed_text 给出图片中实际看到的精确原文。"
            "每个 FAILED check 必须至少有一项同 code 的 asset_findings；OCR_LANGUAGE 失败项还必须"
            "包含 observed_text，逐字引用图片中错误、乱码或残留的可见文字。没有可引用的错误文字"
            "就必须将 OCR_LANGUAGE 判定为 PASSED。不得把语言版本之间的预期差异判为失败。"
            "必须严格使用以下 JSON 结构，三个 checks 不得省略、合并、改名或改变顺序："
            '{"status":"PASSED","checks":['
            '{"code":"FACTUAL_ALIGNMENT","status":"PASSED","evidence":"中文证据"},'
            '{"code":"OCR_LANGUAGE","status":"PASSED","evidence":"中文证据"},'
            '{"code":"DUPLICATION","status":"PASSED","evidence":"中文证据"}],'
            '"asset_findings":[]}。全部检查通过时 asset_findings 必须为空数组。失败项格式示例：'
            '{"qa_asset_id":1,"code":"OCR_LANGUAGE",'
            '"evidence":"中文证据","observed_text":"仅 OCR 失败必填的原文"}。'
            f"\n事实与产物清单：{json.dumps(facts, ensure_ascii=False, sort_keys=True)}"
        )
        prompt += (
            "\nFor an asset with reviewed_local_repair and protected_pixels_verified=true, "
            "the preserved physical product artwork is bound to the approved master image. "
            "Words printed as intrinsic product artwork must remain unchanged; they are not "
            "locale-specific explanatory copy. Check the separately localized footer against "
            "the exact footer_text and the requested locale. Do not fail OCR_LANGUAGE merely "
            "because unchanged intrinsic artwork contains English, and do not ignore missing, "
            "incorrect, unreadable or wrong-language footer text. Also inspect the delivered "
            "image for visual damage or loss of readability after media transcoding."
        )
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        inline_bytes = 0
        for _asset_id, row in batch:
            url, size = _qa_image_reference(row, verified_local=verified_local_assets)
            inline_bytes += size
            if inline_bytes > 30 * 1024 * 1024:
                raise ValueError('verified local QA batch exceeds inline upload limit')
            content.append({"type": "image_url", "image_url": {"url": url}})
        messages = [{"role": "user", "content": content}]
        response = context.chat(
            purpose='translation_quality_assurance' if localized_assets else 'image_quality_assurance',
            model=model, messages=messages,
            business={'offer_id':str(offer_id), 'phase':'localized-qa' if localized_assets else 'master-qa',
                      'brand_id':primary_brand_id, 'batch':batch_number},
            parameters={'max_tokens':1800, 'artifact_digests':digests, 'master_qa_digest':master_qa_digest},
            call=lambda: client.chat_completions(model=model, messages=messages, max_tokens=1800, allow_paid_request=True),
        )
        raw_content = _content_text(response)
        raw_responses.append(
            {
                "batch": batch_number,
                "asset_ids": [item[0] for item in batch],
                "content": raw_content,
            }
        )
        if raw_attempt_path is not None:
            raw_attempt_path.write_text(
                json.dumps(
                    {"model": model, "offer_id": offer_id, "responses": raw_responses},
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        parsed = json.loads(raw_content)
        if not isinstance(parsed, dict):
            raise ValueError("Lingshi QA response must be an object")
        parsed["checks"] = _normalize_visual_checks(parsed.get("checks"))
        _validate_batch_failure_evidence(
            parsed, valid_asset_ids={item[0] for item in batch}
        )
        parsed_batches.append(parsed)
    expected_codes = ("FACTUAL_ALIGNMENT", "OCR_LANGUAGE", "DUPLICATION")
    merged_checks = []
    for index, code in enumerate(expected_codes):
        rows = [batch["checks"][index] for batch in parsed_batches]
        merged_checks.append({
            "code": code,
            "status": "FAILED" if any(row["status"] == "FAILED" for row in rows) else "PASSED",
            "evidence": "；".join(str(row.get("evidence") or "") for row in rows),
        })
    parsed = {
        "status": "FAILED" if any(row["status"] == "FAILED" for row in merged_checks) else "PASSED",
        "checks": merged_checks,
    }
    assessment: dict[str, Any] = {
        "schema_version": ASSESSMENT_SCHEMA,
        "status": parsed.get("status"),
        "offer_id": offer_id,
        "model": model,
        "artifact_digests": digests,
        "round1_snapshot_digest":round1['snapshot_digest'],
        "asset_findings":[{**finding, 'artifact_digest':assets[int(finding['qa_asset_id'])-1]['artifact_digest']}
                          for batch in parsed_batches for finding in batch.get('asset_findings') or []],
        "checks": _normalize_visual_checks(parsed.get("checks")),
    }
    if master_qa_digest:
        assessment["master_image_qa_digest"] = master_qa_digest
    assessment["assessment_digest"] = canonical_digest(assessment)
    return assessment


def _checked_cli_binding(args, *, runtime=None):
    profile = getattr(args, 'binding_profile', None)
    supplied = getattr(args, 'binding_profile_sha256', None)
    if profile is None and supplied is None:
        return None
    if profile is None or supplied is None or runtime is not None:
        raise ValueError('QA_FROZEN_ASSESSMENT_BINDING_REQUIRED')
    if hashlib.sha256(profile.read_bytes()).hexdigest() != supplied:
        raise ValueError('QA_PROFILE_DIGEST_CHANGED')
    from scripts.repo_bound_agent_entry import check_binding, check_arguments
    bound = check_binding(profile, 'qa')
    if bound.get('entry_mode') != 'qa-existing-assessment' or Path(bound['source_root']) != ROOT:
        raise ValueError('QA_SOURCE_BINDING_MISMATCH')
    if bound['profile_sha256'] != supplied:
        raise ValueError('QA_PROFILE_DIGEST_CHANGED')
    if (args.model or args.verified_local_assets or args.paid_policy is not None or args.usage_baseline is not None):
        raise ValueError('QA_EXISTING_ASSESSMENT_MODE_ONLY')
    if args.assessment is None:
        raise ValueError('QA_EXISTING_ASSESSMENT_REQUIRED')
    check_arguments(bound, 'qa', ['--offer-id',args.offer_id,'--assessment',str(args.assessment)])
    return bound


def _check_bound_output(directory):
    from scripts.repo_bound_agent_entry import checked_path
    for path,is_directory in ((directory,True),(directory/'image-qa-attempts',True),
        (directory/'lingshi-image-qa-assessment.json',False),(directory/'automated-image-qa.json',False)):
        if path.exists() or path.is_symlink():
            checked_path(str(path),'QA_OUTPUT',directory=is_directory)
    archive=directory/'image-qa-attempts'
    if archive.is_dir():
        for path in archive.iterdir():
            checked_path(str(path),'QA_OUTPUT_ARCHIVE')


def run(args: argparse.Namespace, *, runtime=None, binding=None) -> dict[str, Any]:
    _runtime_root(runtime)
    if runtime is not None:
        runtime.require_offer(args.offer_id)
    from modules.sourcing.image_generation_checkpoint import atomic_json, business_lock, digest
    from modules.sourcing.new_product_workbench import load_state
    from shared_platform.publication_rounds import validate_round2_input
    from shared_platform.publication_paid_requests import load_paid_context
    offer_id=str(args.offer_id)
    if binding:
        from scripts.repo_bound_agent_entry import checked_path
        if runtime is not None or binding.get('entry_mode') != 'qa-existing-assessment':
            raise ValueError('QA_EXISTING_ASSESSMENT_MODE_ONLY')
        # Missing captured inputs stop before acquiring a lock or creating output.
        for name,path in (
            ('STATE',Path(binding['state_dir'])/(offer_id+'.json')),
            ('R1',Path(binding['round1_reports_root'])/offer_id/'round1-approved-snapshot.json'),
            ('R2_GENERATION',Path(binding['r2_reports_root'])/offer_id/'brand-image-generation.json'),
            ('ASSESSMENT',Path(binding['assessment_path']))):
            checked_path(str(path),'QA_CAPTURED_'+name)
        state=load_state(offer_id,state_dir=Path(binding['state_dir']))
        round1=validate_round2_input(offer_id,state,reports_root=Path(binding['round1_reports_root']))
        input_root=Path(binding['r2_reports_root'])/offer_id
        report_root=Path(binding['qa_output_root'])/offer_id
        phase_root=Path(binding['phase_lock_root'])/offer_id
        _check_bound_output(report_root)
        translation_path=input_root/'brand-image-translation.json'
        if translation_path.exists() or translation_path.is_symlink():
            checked_path(str(translation_path),'QA_CAPTURED_R2_TRANSLATION')
    else:
        state=(load_state(offer_id) if runtime is None else load_state(offer_id, state_dir=runtime.state_dir))
        round1=(validate_round2_input(offer_id,state) if runtime is None else validate_round2_input(offer_id,state, reports_root=runtime.reports_root))
        report_root=_runtime_root(runtime)/'reports/product-preparation'/offer_id
        input_root=phase_root=report_root
    context=None
    if not getattr(args,'assessment',None):
        if not args.model:
            raise ValueError('a governed QA model or existing local assessment is required')
        context=runtime.paid_context if runtime is not None else load_paid_context(offer_id=offer_id,round1=round1,repo_root=_runtime_root(runtime),
            policy_path=getattr(args,'paid_policy',None),usage_baseline_path=getattr(args,'usage_baseline',None))
    phase_digest=digest({'scope':'round2-phase','offer_id':offer_id})
    if binding:
        lock_path=phase_root/f'.lingshi-{phase_digest[:24]}.lock'
        if lock_path.exists() or lock_path.is_symlink():
            checked_path(str(lock_path),'QA_PHASE_LOCK')
    with business_lock(phase_root,phase_digest):
        if binding:
            if _checked_cli_binding(args) != binding:
                raise ValueError('QA_FROZEN_BINDING_CHANGED')
            _check_bound_output(report_root)
            checked_path(str(input_root/'brand-image-generation.json'),'QA_CAPTURED_R2_GENERATION')
            if (input_root/'brand-image-translation.json').exists() or (input_root/'brand-image-translation.json').is_symlink():
                checked_path(str(input_root/'brand-image-translation.json'),'QA_CAPTURED_R2_TRANSLATION')
        generation=_load(input_root/'brand-image-generation.json')
        translation_path=input_root/'brand-image-translation.json'
        translation=_load(translation_path) if translation_path.is_file() else {'assets':[],'approved_tasks':[],'generation_identity_digest':''}
        if generation.get('status')!='BRAND_IMAGE_REVIEW_REQUIRED':
            raise ValueError('QA requires the complete current master set')
        if translation.get('assets') and translation.get('status')!='LOCALIZED_IMAGE_REVIEW_REQUIRED':
            raise ValueError('localized QA requires the complete approved translation set')
        if getattr(args,'assessment',None):
            assessment=_load(args.assessment)
        else:
            context.ensure_ready()
            if runtime is not None:
                generation, translation = runtime.qa_local_documents(generation, translation)
            class LazyClient:
                def chat_completions(self,**kwargs):
                    client = runtime.client(timeout=180) if runtime is not None else LingshiClient.from_config(_runtime_root(runtime)/'config/lingshi.local.json',timeout=180)
                    return client.chat_completions(**kwargs)
            try:
                assessment=_visual_assessment(client=LazyClient(),model=args.model,offer_id=offer_id,round1=round1,
                    generation=generation,translation=translation,raw_attempt_path=report_root/'lingshi-image-qa-raw.json',paid_context=context,
                    verified_local_assets=getattr(args,'verified_local_assets',False))
            finally:
                atomic_json(report_root/'paid-request-summary.json',context.summary())
        assessment=dict(assessment)
        assessment.pop('assessment_digest',None)
        assessment['checks']=_normalize_visual_checks(assessment.get('checks'))
        assessment['assessment_digest']=canonical_digest(assessment)
        atomic_json(report_root/'lingshi-image-qa-assessment.json',assessment)
        receipt=build_automated_image_qa(round1_snapshot=round1,generation=generation,translation=translation,visual_assessment=assessment)
        receipt.pop('qa_digest',None)
        receipt['asset_findings']=assessment.get('asset_findings') or []
        receipt['qa_digest']=canonical_digest(receipt)
        path=persist_automated_image_qa(receipt,path=report_root/'automated-image-qa.json')
        return {'schema_version':'automated-image-qa-result/v1','offer_id':offer_id,'status':receipt['status'],
                'qa_digest':receipt['qa_digest'],'report_path':str(path if binding else path.relative_to(_runtime_root(runtime))), 'platform_writes':0,
                'paid_requests':context.summary() if context else None}


def main(argv=None, *, runtime=None) -> int:
    _runtime_root(runtime)
    parser=argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('--offer-id',required=True)
    parser.add_argument('--model',default=os.environ.get('LINGSHI_IMAGE_QA_MODEL',''))
    parser.add_argument('--assessment',type=Path)
    parser.add_argument('--verified-local-assets',action='store_true',help='Upload only digest-verified local QA image bytes inline')
    parser.add_argument('--paid-policy',type=Path)
    parser.add_argument('--usage-baseline',type=Path)
    parser.add_argument('--binding-profile',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--binding-profile-sha256',help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    binding=_checked_cli_binding(args,runtime=runtime)
    result=run(args, runtime=runtime,binding=binding)
    print(json.dumps(result,ensure_ascii=True,indent=2))
    return 0 if result['status']=='PASSED' else 2


if __name__ == "__main__":
    raise SystemExit(main())
