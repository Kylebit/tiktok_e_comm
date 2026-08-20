"""Independent human review state for paid localized product images.

The review project is bound to one immutable approved-publication snapshot.  It
does not update Product Center, a ReleasePlan, Miaoshou, or a marketplace.  A
successful approval produces only a local publication-image supplement which a
later, separately approved workflow may freeze into a successor ReleasePlan.
"""

from __future__ import annotations

from io import BytesIO
import hashlib
import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image

from modules.sourcing.localized_image_packs import (
    APPROVED_SNAPSHOT_SCHEMA_VERSION,
    LOCALES,
    _approved_images,
    _offer_id,
    _publication_targets,
    _target_locale,
)


SCHEMA_VERSION = "localized-image-review/v1"
SUPPLEMENT_SCHEMA_VERSION = "publication-image-supplement/v1"
DEFAULT_SELECTED_POSITIONS = (1, 5, 6, 7)
REVIEW_LOCALES = tuple(locale for locale in LOCALES if locale != "en-master")
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class LocalizedImageReviewError(ValueError):
    """The review state is incomplete, stale, or ambiguous."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_digest(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        candidate = Path(temporary)
        if candidate.exists():
            candidate.unlink()


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        candidate = Path(temporary)
        if candidate.exists():
            candidate.unlink()


def _png_bytes(raw: object) -> bytes:
    if not isinstance(raw, bytes) or not raw or len(raw) > 20 * 1024 * 1024:
        raise LocalizedImageReviewError("localized image artifact is invalid")
    try:
        with Image.open(BytesIO(raw)) as image:
            image.load()
            output = BytesIO()
            image.convert("RGB").save(output, format="PNG", optimize=True)
            return output.getvalue()
    except Exception as error:
        raise LocalizedImageReviewError("localized image artifact is invalid") from error


def _positions(raw: Sequence[object], image_count: int) -> list[int]:
    if isinstance(raw, (str, bytes)):
        raise LocalizedImageReviewError("selected image positions are required")
    if not raw:
        return []
    try:
        values = [int(value) for value in raw]
    except (TypeError, ValueError) as error:
        raise LocalizedImageReviewError("selected image positions are invalid") from error
    if (
        values != sorted(values)
        or len(values) != len(set(values))
        or any(value < 1 or value > image_count for value in values)
    ):
        raise LocalizedImageReviewError("selected image positions are invalid")
    return values


class LocalizedImageReviewStore:
    """Durable local-only generation and review ledger."""

    def __init__(self, root: Path):
        self.root = Path(root)
        lock_key = str(self.root.resolve())
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(lock_key, threading.RLock())

    def _root(self, offer_id: object) -> Path:
        return self.root / _offer_id(offer_id)

    def _path(self, offer_id: object) -> Path:
        return self._root(offer_id) / "review.json"

    def _artifact_path(self, offer_id: object, artifact_id: str) -> Path:
        return self._root(offer_id) / "artifacts" / f"{artifact_id}.png"

    @staticmethod
    def _check_revision(project: Mapping[str, Any], expected_revision: object) -> None:
        try:
            expected = int(expected_revision)
        except (TypeError, ValueError) as error:
            raise LocalizedImageReviewError("localized review revision is invalid") from error
        if expected != int(project.get("revision") or 0):
            raise LocalizedImageReviewError("localized review revision has changed")

    def _commit(self, project: dict[str, Any]) -> dict[str, Any]:
        project["revision"] = int(project.get("revision") or 0) + 1
        project["updated_at"] = _now()
        project["product_center_mutated"] = False
        project["platform_writes"] = 0
        _atomic_json(self._path(project["offer_id"]), project)
        return project

    def load(self, offer_id: object) -> dict[str, Any]:
        path = self._path(offer_id)
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LocalizedImageReviewError("localized image review is unreadable") from error
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != SCHEMA_VERSION
            or value.get("offer_id") != _offer_id(offer_id)
        ):
            raise LocalizedImageReviewError("localized image review is invalid")
        return value

    def initialize(
        self,
        snapshot: Mapping[str, Any],
        *,
        selected_positions: Sequence[object] = DEFAULT_SELECTED_POSITIONS,
    ) -> dict[str, Any]:
        with self._lock:
            if not isinstance(snapshot, Mapping):
                raise LocalizedImageReviewError("approved snapshot is required")
            if snapshot.get("schema_version") != APPROVED_SNAPSHOT_SCHEMA_VERSION:
                raise LocalizedImageReviewError("approved snapshot schema is unsupported")
            offer_id = _offer_id(snapshot.get("offer_id"))
            plan_id = str(snapshot.get("plan_id") or "").strip()
            snapshot_digest = str(snapshot.get("snapshot_digest") or "").strip()
            if not plan_id or not re.fullmatch(r"sha256:[a-f0-9]{64}", snapshot_digest):
                raise LocalizedImageReviewError("approved snapshot identity is invalid")
            images = _approved_images(snapshot)
            positions = _positions(selected_positions, len(images))
            labels = _publication_targets(snapshot)
            return self._initialize_bound_input(
                offer_id=offer_id,
                binding_id=plan_id,
                input_digest=snapshot_digest,
                images=images,
                positions=positions,
                labels=labels,
                input_schema_version=APPROVED_SNAPSHOT_SCHEMA_VERSION,
            )

    def initialize_from_first_review(
        self,
        *,
        offer_id: object,
        first_review_id: object,
        input_digest: object,
        ordered_images: Sequence[object],
        selected_positions: Sequence[object],
        publication_targets: Sequence[object],
        target_locales: Sequence[object],
    ) -> dict[str, Any]:
        """Freeze an approved first review before a ReleasePlan exists."""

        with self._lock:
            clean_offer_id = _offer_id(offer_id)
            clean_review_id = str(first_review_id or "").strip()
            clean_digest = str(input_digest or "").strip()
            if not re.fullmatch(r"first-review:[a-f0-9]{20}", clean_review_id):
                raise LocalizedImageReviewError("first-review identity is invalid")
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", clean_digest):
                raise LocalizedImageReviewError("first-review digest is invalid")
            images = [str(value or "").strip() for value in ordered_images]
            if (
                not images
                or any(not value.startswith("https://") for value in images)
                or len(images) != len(set(images))
            ):
                raise LocalizedImageReviewError("first-review images are invalid")
            positions = _positions(selected_positions, len(images))
            labels = [str(value or "").strip() for value in publication_targets]
            if not labels or len(labels) != len(set(labels)):
                raise LocalizedImageReviewError("first-review targets are invalid")
            for label in labels:
                _target_locale(label)
            locales = [str(value or "").strip() for value in target_locales]
            if (
                (positions and not locales)
                or len(locales) != len(set(locales))
                or any(locale not in REVIEW_LOCALES for locale in locales)
            ):
                raise LocalizedImageReviewError("first-review locales are invalid")
            return self._initialize_bound_input(
                offer_id=clean_offer_id,
                binding_id=clean_review_id,
                input_digest=clean_digest,
                images=images,
                positions=positions,
                labels=labels,
                input_schema_version="approved-first-review-image-input/v1",
                allowed_locales=locales,
            )

    def _initialize_bound_input(
        self,
        *,
        offer_id: str,
        binding_id: str,
        input_digest: str,
        images: list[str],
        positions: list[int],
        labels: list[str],
        input_schema_version: str,
        allowed_locales: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        routes = {label: _target_locale(label) for label in labels}
        allowed = set(allowed_locales or REVIEW_LOCALES)
        locales = [
            locale
            for locale in REVIEW_LOCALES
            if locale in set(routes.values()) and locale in allowed
        ]
        if positions and not locales:
            raise LocalizedImageReviewError("localized publication targets are missing")
        existing = self.load(offer_id)
        if existing:
            if (
                existing.get("approved_snapshot_digest") == input_digest
                and existing.get("selected_positions") == positions
                and existing.get("input_schema_version")
                    in {None, input_schema_version}
            ):
                return existing
            raise LocalizedImageReviewError(
                "localized image review belongs to a different approved input"
            )
        tasks: list[dict[str, Any]] = []
        for position in positions:
            source_url = images[position - 1]
            for locale in locales:
                identity = {
                    "offer_id": offer_id,
                    "approved_snapshot_digest": input_digest,
                    "position": position,
                    "source_url": source_url,
                    "locale": locale,
                }
                tasks.append(
                    {
                        "task_id": f"localized-review-{_canonical_digest(identity)[7:27]}",
                        "position": position,
                        "source_url": source_url,
                        "source_url_digest": _canonical_digest(source_url),
                        "locale": locale,
                        "target_labels": [
                            label for label, target_locale in routes.items()
                            if target_locale == locale
                        ],
                        "status": "PENDING_GENERATION",
                        "decision": "PENDING",
                        "artifact_id": None,
                        "output_digest": None,
                        "generation_receipt": None,
                        "translations": [],
                    }
                )
        created_at = _now()
        project = {
            "schema_version": SCHEMA_VERSION,
            "input_schema_version": input_schema_version,
            "offer_id": offer_id,
            "revision": 1,
            "status": "PENDING_GENERATION" if tasks else "READY_FOR_REVIEW",
            "release_plan_id": binding_id,
            "approved_snapshot_digest": input_digest,
            "approved_ordered_images": images,
            "selected_positions": positions,
            "route_locales": routes,
            "tasks": tasks,
            "paid_generation_budget": len(tasks),
            "external_generation_count": 0,
            "product_center_mutated": False,
            "platform_writes": 0,
            "created_at": created_at,
            "updated_at": created_at,
        }
        _atomic_json(self._path(offer_id), project)
        return project

    def save_generation_bundle(
        self,
        offer_id: object,
        *,
        expected_revision: object,
        items: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Bind one paid result for every task currently requiring generation."""

        with self._lock:
            project = self.load(offer_id)
            if not project:
                raise LocalizedImageReviewError("localized image review is missing")
            self._check_revision(project, expected_revision)
            pending = {
                row["task_id"]: row for row in project["tasks"]
                if row.get("status") in {"PENDING_GENERATION", "RETRY_REQUESTED"}
            }
            if not pending:
                return project
            incoming = {
                str(row.get("task_id") or ""): row
                for row in items if isinstance(row, Mapping)
            }
            if set(incoming) != set(pending) or len(incoming) != len(items):
                raise LocalizedImageReviewError("localized generation task coverage changed")

            prepared: dict[str, dict[str, Any]] = {}
            for task_id, task in pending.items():
                item = incoming[task_id]
                artifact = _png_bytes(item.get("image_bytes"))
                digest = f"sha256:{hashlib.sha256(artifact).hexdigest()}"
                receipt = item.get("receipt")
                translations = item.get("translations")
                public_url = str((receipt or {}).get("public_url") or "").strip()
                if (
                    not isinstance(receipt, Mapping)
                    or receipt.get("status") != "COMPLETED"
                    or receipt.get("provider") != "toapis-images/v1"
                    or receipt.get("model") != "gpt-image-2-official"
                    or not str(receipt.get("task_id") or "").strip()
                    or not str(receipt.get("client_business_id") or "").strip()
                    or receipt.get("request_attempted") is not True
                    or receipt.get("outcome_unknown") is not False
                    or int(receipt.get("external_generation_count") or 0) != 1
                    or receipt.get("output_digest") not in {None, digest}
                    or (public_url and not public_url.startswith("https://"))
                    or not isinstance(translations, list)
                    or not translations
                ):
                    raise LocalizedImageReviewError("localized generation receipt is invalid")
                artifact_id = f"localized-review-{digest[7:27]}"
                prepared[task_id] = {
                    "artifact": artifact,
                    "artifact_id": artifact_id,
                    "output_digest": digest,
                    "translations": [dict(row) for row in translations],
                    "generation_receipt": {
                        "status": "COMPLETED",
                        "provider": "toapis-images/v1",
                        "model": "gpt-image-2-official",
                        "task_id": str(receipt["task_id"]),
                        "client_business_id": str(receipt["client_business_id"]),
                        "request_attempted": True,
                        "outcome_unknown": False,
                        "external_generation_count": 1,
                        "output_digest": digest,
                        "public_url": public_url or None,
                    },
                }

            for row in prepared.values():
                path = self._artifact_path(offer_id, row["artifact_id"])
                if path.exists() and path.read_bytes() != row["artifact"]:
                    raise LocalizedImageReviewError("localized artifact identity collision")
                if not path.exists():
                    _atomic_bytes(path, row["artifact"])
            for task in project["tasks"]:
                result = prepared.get(task["task_id"])
                if not result:
                    continue
                task.update(
                    status="READY_FOR_REVIEW",
                    decision="PENDING",
                    artifact_id=result["artifact_id"],
                    output_digest=result["output_digest"],
                    translations=result["translations"],
                    generation_receipt=result["generation_receipt"],
                    generated_at=_now(),
                )
            project["external_generation_count"] = int(
                project.get("external_generation_count") or 0
            ) + len(prepared)
            project["status"] = "REVIEW_REQUIRED"
            project.pop("approval", None)
            project.pop("publication_supplement", None)
            if project.get("approval_intent"):
                self._reconcile_approval_intent(project)
            return self._commit(project)

    def decide(
        self,
        offer_id: object,
        *,
        expected_revision: object,
        task_id: object,
        decision: object,
    ) -> dict[str, Any]:
        with self._lock:
            project = self.load(offer_id)
            if not project:
                raise LocalizedImageReviewError("localized image review is missing")
            self._check_revision(project, expected_revision)
            if project.get("status") == "APPROVED":
                raise LocalizedImageReviewError("approved localized review is immutable")
            matches = [
                row for row in project["tasks"]
                if row.get("task_id") == str(task_id or "").strip()
            ]
            if len(matches) != 1:
                raise LocalizedImageReviewError("localized review task is unavailable")
            task = matches[0]
            clean = str(decision or "").strip().upper()
            if clean not in {"PASS", "RETRY"}:
                raise LocalizedImageReviewError("localized review decision is invalid")
            if task.get("status") not in {"READY_FOR_REVIEW", "PASSED"}:
                raise LocalizedImageReviewError("localized review task is not ready")
            if clean == "PASS":
                task["status"] = "PASSED"
                task["decision"] = "PASS"
                task["reviewed_at"] = _now()
            else:
                task.update(
                    status="RETRY_REQUESTED",
                    decision="RETRY",
                    artifact_id=None,
                    output_digest=None,
                    generation_receipt=None,
                    translations=[],
                    reviewed_at=_now(),
                )
            project["status"] = "REVIEW_REQUIRED"
            return self._commit(project)

    def record_miaoshou_pre_review_sync(
        self,
        offer_id: object,
        *,
        expected_revision: object,
        receipt: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Bind one verified common-draft write before human image review."""

        with self._lock:
            project = self.load(offer_id)
            if not project:
                raise LocalizedImageReviewError("localized image review is missing")
            self._check_revision(project, expected_revision)
            if any(
                row.get("status") not in {"READY_FOR_REVIEW", "PASSED"}
                for row in project["tasks"]
            ):
                raise LocalizedImageReviewError(
                    "localized images must be generated before Miaoshou sync"
                )
            if not isinstance(receipt, Mapping):
                raise LocalizedImageReviewError("Miaoshou pre-review receipt is invalid")
            try:
                external_write_count = int(receipt.get("external_write_count") or 0)
                written_image_count = int(receipt.get("written_image_count") or 0)
            except (TypeError, ValueError) as error:
                raise LocalizedImageReviewError(
                    "Miaoshou pre-review receipt is invalid"
                ) from error
            if (
                receipt.get("status") != "VERIFIED"
                or receipt.get("written_to_miaoshou") is not True
                or receipt.get("verified") is not True
                or external_write_count != 1
                or written_image_count < 1
                or receipt.get("claimed") is not False
                or receipt.get("published") is not False
            ):
                raise LocalizedImageReviewError(
                    "Miaoshou pre-review receipt is invalid"
                )
            project["miaoshou_pre_review_sync"] = {
                "status": "VERIFIED",
                "written_to_miaoshou": True,
                "verified": True,
                "external_write_count": 1,
                "written_image_count": written_image_count,
                "claimed": False,
                "published": False,
                "verified_at": _now(),
            }
            return self._commit(project)

    def approve(
        self,
        offer_id: object,
        *,
        expected_revision: object,
        approved_by: object,
    ) -> dict[str, Any]:
        with self._lock:
            project = self.load(offer_id)
            if not project:
                raise LocalizedImageReviewError("localized image review is missing")
            self._check_revision(project, expected_revision)
            actor = str(approved_by or "").strip()
            if not actor or len(actor) > 80:
                raise LocalizedImageReviewError("localized review approver is invalid")
            intent_identity = {
                "schema_version": "localized-image-approval-intent/v1",
                "offer_id": project["offer_id"],
                "release_plan_id": project["release_plan_id"],
                "approved_snapshot_digest": project["approved_snapshot_digest"],
                "selected_positions": project["selected_positions"],
                "approved_by": actor,
                "auto_accept_ready_assets": True,
            }
            project["approval_intent"] = {
                **intent_identity,
                "intent_digest": _canonical_digest(intent_identity),
                "recorded_at": _now(),
            }
            self._reconcile_approval_intent(project)
            return self._commit(project)

    def _reconcile_approval_intent(self, project: dict[str, Any]) -> None:
        intent = project.get("approval_intent") or {}
        if intent.get("auto_accept_ready_assets") is not True:
            raise LocalizedImageReviewError("localized approval intent is invalid")
        for row in project["tasks"]:
            if row.get("status") == "READY_FOR_REVIEW":
                row["status"] = "PASSED"
                row["decision"] = "CHAT_APPROVED"
                row["reviewed_at"] = _now()
        if any(row.get("status") != "PASSED" for row in project["tasks"]):
            project["status"] = "APPROVAL_RECORDED"
            project.pop("approval", None)
            project.pop("publication_supplement", None)
            return

        actor = str(intent.get("approved_by") or "").strip()
        if not actor:
            raise LocalizedImageReviewError("localized approval intent is invalid")
        approved_tasks = [
            {
                key: row.get(key)
                for key in (
                    "task_id", "position", "source_url", "source_url_digest",
                    "locale", "target_labels", "artifact_id", "output_digest",
                    "generation_receipt",
                )
            }
            for row in project["tasks"]
        ]
        identity = {
            "schema_version": "localized-image-approval/v1",
            "offer_id": project["offer_id"],
            "release_plan_id": project["release_plan_id"],
            "approved_snapshot_digest": project["approved_snapshot_digest"],
            "selected_positions": project["selected_positions"],
            "approved_by": actor,
            "tasks": approved_tasks,
        }
        approval = {
            **identity,
            "approval_digest": _canonical_digest(identity),
            "approved_at": _now(),
        }
        project["approval"] = approval
        project["publication_supplement"] = self._build_supplement(project, approval)
        project["status"] = "APPROVED"

    @staticmethod
    def _build_supplement(
        project: Mapping[str, Any], approval: Mapping[str, Any]
    ) -> dict[str, Any]:
        task_by_key = {
            (int(row["position"]), str(row["locale"])): row
            for row in approval["tasks"]
        }
        routes: dict[str, Any] = {}
        base = list(project["approved_ordered_images"])
        for target_label, locale in project["route_locales"].items():
            images: list[dict[str, Any]] = []
            for position, source_url in enumerate(base, start=1):
                task = task_by_key.get((position, locale))
                if task:
                    images.append(
                        {
                            "position": position,
                            "kind": "LOCALIZED_ARTIFACT",
                            "artifact_id": task["artifact_id"],
                            "artifact_digest": task["output_digest"],
                            "source_url": source_url,
                        }
                    )
                else:
                    images.append(
                        {
                            "position": position,
                            "kind": "APPROVED_BASE_URL",
                            "url": source_url,
                        }
                    )
            routes[target_label] = {"locale": locale, "ordered_images": images}
        identity = {
            "schema_version": SUPPLEMENT_SCHEMA_VERSION,
            "offer_id": project["offer_id"],
            "release_plan_id": project["release_plan_id"],
            "approved_snapshot_digest": project["approved_snapshot_digest"],
            "approval_digest": approval["approval_digest"],
            "routes": routes,
        }
        return {
            **identity,
            "supplement_digest": _canonical_digest(identity),
            "status": "APPROVED_LOCAL_ASSETS",
            "platform_writes": 0,
            "product_center_mutated": False,
        }

    def artifact_path(self, offer_id: object, artifact_id: object) -> Path:
        project = self.load(offer_id)
        clean = str(artifact_id or "").strip()
        if not re.fullmatch(r"localized-review-[a-f0-9]{20}", clean):
            raise LocalizedImageReviewError("localized artifact_id is invalid")
        if not any(row.get("artifact_id") == clean for row in project.get("tasks") or []):
            raise LocalizedImageReviewError("localized artifact is not bound")
        path = self._artifact_path(offer_id, clean)
        if not path.is_file():
            raise LocalizedImageReviewError("localized artifact is missing")
        return path
