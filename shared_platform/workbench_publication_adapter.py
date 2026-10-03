"""Observe original publication authority without manufacturing approvals.

The adapter reads the exact registered R1/R2 product and the original server's
validated final-plan/run/readback projection. It never publishes, generates an
image, writes a keep decision, or substitutes a task note for domain approval.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import quote


class PublicationReconciliationRequired(ValueError):
    """The domain reported an unresolved external result, not a new write."""


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _offer(task):
    value = (task.get("scope") or {}).get("offer_id")
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or len(value) > 32:
        raise ValueError("上架任务需要明确的采集箱 ID")
    return value


def _read_product(offer, profile):
    from shared_platform.publication_review_projection import dashboard
    from shared_platform.publication_r2_review import review_view
    return dashboard(offer, runtime_root=profile.root), review_view(offer, runtime_root=profile.root)


def _read_release(offer, profile):
    from modules.products import server
    if Path(server.ROOT).resolve() != Path(profile.root).resolve():
        raise ValueError("原上架服务与任务版本不一致，不能读取其他版本的发布状态")
    status, payload = server._publication_stages_for_request({"offer_id": offer})
    if status != 200 or payload.get("ok") is not True or payload.get("offer_id") != offer:
        raise ValueError("原发布流程尚未提供有效状态，请查看上架页的具体缺口")
    return payload


def _identity(task, product, images):
    from shared_platform.internal_catalog_sku import internal_sku
    offer = _offer(task)
    first = product.get("frozen_first_review") or {}
    targets = images.get("targets")
    if (product.get("frozen_review_projection") is not True or first.get("offer_id") != offer
            or images.get("offer_id") != offer or not first.get("snapshot_digest")
            or first.get("approved_revision") != images.get("approved_revision")
            or not images.get("binding_sha256") or not isinstance(targets, list) or not targets
            or len(set(targets)) != len(targets)
            or [r.get("target") for r in first.get("targets", [])] != targets):
        raise ValueError("冻结商品、图片和目标身份不一致，不能继续")
    requested = task.get("scope") or {}
    # User-supplied target strings must match the domain's frozen target labels.
    # Unresolved display names cannot silently expand to the registered scope.
    if requested.get("shops") and set(requested["shops"]) != set(targets):
        raise ValueError("任务店铺范围与原审核冻结范围不一致，需要执行者核实准确身份")
    catalog_sku = internal_sku(images.get("seller_sku"))
    if len(catalog_sku) != 4 or not catalog_sku.isascii() or not catalog_sku.isdigit():
        raise ValueError("原审核 SKU 不能唯一映射到内部商品身份")
    if requested.get("skus") and requested["skus"] != [catalog_sku]:
        raise ValueError("任务 SKU 与原审核冻结商品不一致")
    return {"offer_id": offer, "snapshot_digest": first["snapshot_digest"],
            "approved_revision": first["approved_revision"], "binding_sha256": images["binding_sha256"],
            "targets": targets, "seller_sku": images.get("seller_sku"), "internal_sku": catalog_sku}


def _checkpoint(task, step):
    return next((r.get("checkpoint") or {} for r in task.get("steps", []) if r.get("key") == step), {})


def _image_receipt(images):
    rows = images.get("images")
    if (not isinstance(rows, list) or not rows or any(r.get("action") != "keep" for r in rows)
            or images.get("keep_count") != len(rows)
            or (images.get("r2_consumer") or {}).get("status") != "PASSED"
            or not (images.get("r2_consumer") or {}).get("identity")):
        return None
    return {"revision": images["revision"], "binding_sha256": images["binding_sha256"],
            "consumer_identity": images["r2_consumer"]["identity"],
            "selection_digest": _digest(rows)}


def _release_receipt(release, identity, *, require_readback=False):
    market = release.get("marketplace") or {}
    plan = market.get("plan") or {}
    final = market.get("final_review") or {}
    rows = market.get("target_results") or []
    summary = market.get("execution_summary") or {}
    if (market.get("status") == "RECONCILIATION_REQUIRED"
            or (release.get("common") or {}).get("status") == "RECONCILIATION_REQUIRED"
            or summary.get("blockers") or market.get("blockers")
            or any(r.get("outcome_unknown") is True or r.get("status") == "RECONCILIATION_REQUIRED" for r in rows)):
        raise PublicationReconciliationRequired("发布结果尚未核实或证据异常，需要执行者对账；不能重复发布或重新批准")
    if plan and (plan.get("product_id") != identity["offer_id"]
                 or set(plan.get("targets") or []) != set(identity["targets"])
                 or plan.get("status") == "SUPERSEDED"):
        raise ValueError("发布计划商品或目标与任务不一致，或原计划已被替代")
    if release.get("current_r2_matches_approved") is False:
        raise ValueError("当前图片与已批准发布快照不一致，需要执行者核查")
    if (release.get("offer_id") != identity["offer_id"]
            or release.get("current_r2_matches_approved") is not True
            or (release.get("common") or {}).get("status") != "VERIFIED"
            or plan.get("status") != "APPROVED" or plan.get("product_id") != identity["offer_id"]
            or set(plan.get("targets") or []) != set(identity["targets"])
            or final.get("approval_recorded") is not True or not final.get("approval_id")
            or not final.get("candidate_digest") or final.get("execution_recorded") is not True
            or summary.get("index_valid") is not True or summary.get("blockers")
            or not isinstance(rows, list) or len(rows) != len(identity["targets"])
            or {r.get("target_label") for r in rows} != set(identity["targets"])
            or not all(r.get("source") and (r.get("request_attempted") is True or r.get("official_success") is True) for r in rows)):
        return None
    if require_readback and not all(r.get("official_success") is True and r.get("readback_completed") is True
                                    and r.get("outcome_unknown") is False for r in rows):
        return None
    return {"plan_id": plan["plan_id"], "approval_id": final["approval_id"],
            "candidate_digest": final["candidate_digest"],
            "target_sources": [{"target_label": r["target_label"], "source": r["source"]} for r in rows],
            "provider_readback_ref": "publication-stages:" + _digest(rows) if require_readback else None}


def _inspect(task, profile):
    offer = _offer(task)
    product, images = _read_product(offer, profile)
    identity = _identity(task, product, images)
    frozen = _checkpoint(task, "facts").get("publication_identity")
    if frozen and frozen != identity:
        raise ValueError("原审核冻结身份已变化，保留已完成步骤并停止接续")
    image_receipt = _image_receipt(images)
    adopted = _checkpoint(task, "images").get("image_receipt")
    if adopted and adopted != image_receipt:
        raise ValueError("已采用图片发生变化或被撤回，需要重新审核，不能沿用旧回执")
    return identity, image_receipt


def _ready(task, profile):
    identity, images = _inspect(task, profile)
    step = task["current_step"]
    if step == "facts":
        return identity, {"publication_identity": identity}
    if step == "images":
        return identity, {"publication_identity": identity, "image_receipt": images} if images else None
    if not images:
        return identity, None
    release = _read_release(identity["offer_id"], profile)
    receipt = _release_receipt(release, identity, require_readback=step == "readback")
    previous = _checkpoint(task, "release").get("release_receipt")
    if previous and receipt and any(previous.get(k) != receipt.get(k) for k in ("plan_id", "approval_id", "candidate_digest")):
        raise ValueError("发布计划或最终批准已变化，不能拼接其他版本的回读")
    return identity, {"publication_identity": identity, "image_receipt": images,
                      "release_receipt": receipt, "provider_readback_ref": receipt.get("provider_readback_ref")} if receipt else None


def run(engine, task, token, profile):
    """Advance verified local stages, or link directly to the original review."""
    task_id, step = task["task_id"], task["current_step"]
    try:
        identity, receipt = _ready(task, profile)
    except PublicationReconciliationRequired as error:
        engine.observe_reconciliation(task_id, token, str(error))
        return
    except (ValueError, OSError, KeyError, TypeError) as error:
        engine.fail(task_id, token, "上架证据校验未通过：" + str(error))
        return
    review_round = {'facts': 'first', 'images': 'images', 'release': 'final', 'readback': 'final'}[step]
    url = "/product-workspace?offer_id=" + quote(identity["offer_id"]) + '&round=' + review_round + '#originalPublicationReview'
    if receipt:
        if step == "facts":
            try:
                engine.bind_scope(task_id, token, {**task["scope"], "skus": [identity["internal_sku"]],
                                                   "shops": identity["targets"]})
            except (ValueError, TypeError) as error:
                engine.fail(task_id, token, "商品范围暂不能接管：" + str(error))
                return
        engine.complete_step(task_id, token, expected_step=step, checkpoint=receipt,
                             result_url=url if step == "readback" else "")
        return
    labels = {"images": ("审核商品图片", "在原上架页保存图片选择；仅全部明确保留且图片合同通过后继续。"),
              "release": ("继续上架审核与发布", "沿用原页面的最终审核与发布操作；已批准的范围不重复批准，未执行不算发布完成。"),
              "readback": ("查看发布结果与回读", "部分平台尚未完成正式回读，请在原上架页处理对应平台结果。")}
    label, reason = labels[step]
    binding = {"adapter": "publication/v1", "step": step, "identity": identity}
    waiting_platform = step == "readback"
    if step == "release":
        release = _read_release(identity["offer_id"], profile)
        final = release.get("marketplace", {}).get("final_review") or {}
        waiting_platform = final.get("approval_recorded") is True and final.get("execution_recorded") is True
    if waiting_platform:
        engine.await_domain(task_id, token, label="等待平台结果", reason="已提交的发布任务正在等待正式结果，工作台会继续读取原发布回执。",
                            url=url, receipt_binding=binding)
    else:
        engine.wait_for_user(task_id, token, kind="review", label=label, reason=reason, url=url, receipt_binding=binding)


def observe(engine, task, profile):
    """Re-read original domain evidence; changed snapshots cannot resume old work."""
    if task.get("template") != "publication" or task.get("execution_state") not in {"waiting_user", "waiting_domain"}:
        return False
    action = task.get("required_action") or task.get("pending_observation") or {}
    binding = action.get("receipt_binding") or {}
    if action.get("kind") not in {"review", "observe"} or binding.get("adapter") != "publication/v1":
        return False
    try:
        identity, checkpoint = _ready(task, profile)
    except PublicationReconciliationRequired as error:
        engine.observe_reconciliation(task["task_id"], None, str(error), action_id=action["action_id"])
        return False
    if not checkpoint or identity != binding.get("identity") or task["current_step"] != binding.get("step"):
        return False
    receipt = {"receipt_id": "publication:" + _digest([task["task_id"], binding, checkpoint]),
               "action_id": action["action_id"], "checkpoint": checkpoint}
    def verify(incoming, frozen):
        # The engine already holds its transaction here. Never open another
        # engine connection from the verifier; only re-read domain sources.
        if frozen != binding:
            return False
        fresh_identity, fresh = _ready(task, profile)
        return fresh is not None and fresh_identity == frozen["identity"] and fresh == incoming["checkpoint"]
    try:
        return engine.accept_domain_receipt(task["task_id"], receipt, verify)
    except PublicationReconciliationRequired as error:
        engine.observe_reconciliation(task["task_id"], None, str(error), action_id=action["action_id"])
        return False
