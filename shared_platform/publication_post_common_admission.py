"""Fail-closed, read-only admission for a new original-page R3 final decision.

The current server has no authoritative offer-wide COMMON write-attempt budget.
This diagnostic therefore cannot grant approval, even for a locally coherent
candidate and a successful single-run official readback.
"""

from __future__ import annotations

from typing import Mapping

from shared_platform.publication_autopilot import _canonical_digest


def inspect_post_common_final_review(*, offer_id, market, common_plan,
                                     common_run, official_readback_blockers):
    market = market if isinstance(market, Mapping) else {}
    plan = market.get('plan')
    plan = plan if isinstance(plan, Mapping) else {}
    payload = plan.get('payload')
    payload = payload if isinstance(payload, Mapping) else {}
    binding = payload.get('r3_marketplace_binding')
    binding = binding if isinstance(binding, Mapping) else {}
    candidate = market.get('preview')
    candidate = candidate if isinstance(candidate, Mapping) else {}
    manifest = candidate.get('review_manifest')
    manifest = manifest if isinstance(manifest, Mapping) else {}
    blockers = []

    common_plan = common_plan if isinstance(common_plan, Mapping) else {}
    common_run = common_run if isinstance(common_run, Mapping) else {}
    rows = common_run.get('targets')
    common_rows = ([row for row in rows if isinstance(row, Mapping)
                    and row.get('target_label') == 'miaoshou:COMMON']
                   if type(rows) is list else [])
    durable_readback = common_rows[0].get('readback') if len(common_rows) == 1 else None
    if (not common_plan or common_plan.get('status') != 'APPROVED'
            or common_plan.get('product_id') != offer_id
            or binding.get('common_plan_id') != common_plan.get('plan_id')
            or binding.get('common_payload_digest') != common_plan.get('payload_digest')
            or binding.get('common_run_id') != common_run.get('run_id')
            or common_run.get('plan_id') != common_plan.get('plan_id')
            or len(common_rows) != 1 or common_rows[0].get('status') != 'SUCCEEDED'
            or binding.get('common_readback') != durable_readback
            or official_readback_blockers):
        blockers.append('COMMON_OFFICIAL_FIELD_READBACK_UNPROVEN')

    targets = plan.get('targets')
    payload_targets = payload.get('targets')
    market_targets = market.get('targets')
    candidate_targets = candidate.get('target_labels')
    manifest_rows = manifest.get('targets')
    manifest_targets = ([row.get('target_label') for row in manifest_rows]
                        if type(manifest_rows) is list
                        and all(isinstance(row, Mapping) for row in manifest_rows) else None)
    material = dict(candidate)
    supplied_candidate_digest = material.pop('candidate_digest', None)
    manifest_material = dict(manifest)
    supplied_manifest_digest = manifest_material.pop('manifest_digest', None)
    try:
        digests_match = (supplied_candidate_digest == _canonical_digest(material)
                         and supplied_manifest_digest == _canonical_digest(manifest_material))
    except (TypeError, ValueError):
        digests_match = False
    if (type(targets) is not list or not targets
            or any(type(label) is not str or not label or label == 'miaoshou:COMMON'
                   for label in targets)
            or len(set(targets)) != len(targets)
            or payload_targets != targets or market_targets != targets
            or candidate_targets != targets or manifest_targets != targets
            or plan.get('product_id') != offer_id
            or candidate.get('offer_id') != offer_id
            or candidate.get('status') != 'READY_FOR_FINAL_REVIEW'
            or manifest.get('schema_version') != 'publication-final-review-manifest/v1'
            or not manifest.get('variants') or not manifest.get('copy_sets')
            or not manifest.get('image_sets') or not digests_match):
        blockers.append('POST_COMMON_COMPLETE_TARGET_MATRIX_UNPROVEN')

    # There is no server-owned cumulative reservation ledger in the current
    # stage view or ReleaseStore contract. Never infer it from one run's status.
    blockers.append('COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN')
    return {'schema_version': 'post-common-final-review-admission/v1',
            'status': 'BLOCKED', 'blockers': blockers,
            'final_review_available': False, 'execution_authority': False,
            'external_writes_performed': []}
