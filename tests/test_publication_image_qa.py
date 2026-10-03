import json

from shared_platform.publication_image_qa import (
    build_automated_image_qa,
    persist_automated_image_qa,
)
from shared_platform.publication_rounds import canonical_digest


def _fixture():
    round1 = {
        "offer_id": "123",
        "snapshot_digest": "sha256:" + "a" * 64,
        "image_plan": {
            "brand_plans": [{
                "id": "livelyhive-sea",
                "generated_assets": [
                    {"role": "cover", "quantity": 1},
                    {"role": "detail", "quantity": 1},
                ],
            }]
        },
    }
    generation = {
        "assets": [
            {"brand_id": "livelyhive-sea", "role": "cover", "status": "COMPLETED", "artifact_digest": "sha256:g1"},
            {"brand_id": "livelyhive-sea", "role": "detail", "status": "COMPLETED", "artifact_digest": "sha256:g2"},
        ]
    }
    translation = {
        "generation_identity_digest": "sha256:generation",
        "approved_tasks": [{
            "review_number": 2,
            "brand_id": "livelyhive-sea",
            "role": "detail",
            "locale": "th-TH",
        }],
        "assets": [{
            "source_review_number": 2,
            "brand_id": "livelyhive-sea",
            "role": "detail",
            "locale": "th-TH",
            "status": "COMPLETED",
            "artifact_digest": "sha256:t1",
        }],
    }
    assessment = {
        "schema_version": "lingshi-image-qa-assessment/v1",
        "status": "PASSED",
        "offer_id": "123",
        "artifact_digests": ["sha256:g1", "sha256:g2", "sha256:t1"],
        "checks": [
            {"code": "FACTUAL_ALIGNMENT", "status": "PASSED"},
            {"code": "OCR_LANGUAGE", "status": "PASSED"},
            {"code": "DUPLICATION", "status": "PASSED"},
        ],
    }
    assessment["assessment_digest"] = canonical_digest(assessment)
    return round1, generation, translation, assessment


def test_round2_qa_binds_visual_assessment_to_all_exact_artifacts():
    receipt = build_automated_image_qa(
        round1_snapshot=_fixture()[0],
        generation=_fixture()[1],
        translation=_fixture()[2],
        visual_assessment=_fixture()[3],
    )

    assert receipt["status"] == "PASSED"
    assert [row["code"] for row in receipt["checks"]] == [
        "ROLE_COVERAGE",
        "FACTUAL_ALIGNMENT",
        "OCR_LANGUAGE",
        "DUPLICATION",
        "TARGET_ROUTING",
    ]
    assert receipt["qa_digest"].startswith("sha256:")


def test_round2_qa_fails_when_provider_assessment_omits_one_artifact():
    round1, generation, translation, assessment = _fixture()
    assessment["artifact_digests"] = assessment["artifact_digests"][:-1]
    unsigned = dict(assessment)
    unsigned.pop("assessment_digest")
    assessment["assessment_digest"] = canonical_digest(unsigned)

    receipt = build_automated_image_qa(
        round1_snapshot=round1,
        generation=generation,
        translation=translation,
        visual_assessment=assessment,
    )

    assert receipt["status"] == "FAILED"
    assert next(row for row in receipt["checks"] if row["code"] == "OCR_LANGUAGE")["status"] == "FAILED"


def test_round2_qa_accepts_mapping_checks_and_preserves_individual_results():
    round1, generation, translation, assessment = _fixture()
    assessment["status"] = "FAILED"
    assessment["checks"] = {
        "FACTUAL_ALIGNMENT": {"status": "PASSED", "evidence": "facts match"},
        "OCR_LANGUAGE": {"status": "FAILED", "evidence": "wrong locale"},
        "DUPLICATION": {"status": "PASSED", "evidence": "distinct"},
    }
    unsigned = dict(assessment)
    unsigned.pop("assessment_digest")
    assessment["assessment_digest"] = canonical_digest(unsigned)

    receipt = build_automated_image_qa(
        round1_snapshot=round1,
        generation=generation,
        translation=translation,
        visual_assessment=assessment,
    )

    statuses = {row["code"]: row["status"] for row in receipt["checks"]}
    assert receipt["status"] == "FAILED"
    assert statuses["FACTUAL_ALIGNMENT"] == "PASSED"
    assert statuses["OCR_LANGUAGE"] == "FAILED"
    assert statuses["DUPLICATION"] == "PASSED"


def test_round2_qa_archives_a_superseded_signed_attempt(tmp_path):
    round1, generation, translation, assessment = _fixture()
    first = build_automated_image_qa(
        round1_snapshot=round1,
        generation=generation,
        translation=translation,
        visual_assessment=assessment,
    )
    path = tmp_path / "automated-image-qa.json"
    persist_automated_image_qa(first, path=path)

    assessment["status"] = "FAILED"
    assessment["checks"][1]["status"] = "FAILED"
    unsigned = dict(assessment)
    unsigned.pop("assessment_digest")
    assessment["assessment_digest"] = canonical_digest(unsigned)
    second = build_automated_image_qa(
        round1_snapshot=round1,
        generation=generation,
        translation=translation,
        visual_assessment=assessment,
    )
    persist_automated_image_qa(second, path=path)

    archived = tmp_path / "image-qa-attempts" / f"{first['qa_digest'].removeprefix('sha256:')}.json"
    assert archived.is_file()
    assert json.loads(archived.read_text(encoding="utf-8"))["qa_digest"] == first["qa_digest"]
    assert json.loads(path.read_text(encoding="utf-8"))["qa_digest"] == second["qa_digest"]


def test_round2_qa_accepts_provider_name_alias_for_check_code():
    round1, generation, translation, assessment = _fixture()
    assessment["checks"] = [
        {"name": row["code"], "status": row["status"]}
        for row in assessment["checks"]
    ]
    unsigned = dict(assessment)
    unsigned.pop("assessment_digest")
    assessment["assessment_digest"] = canonical_digest(unsigned)

    receipt = build_automated_image_qa(
        round1_snapshot=round1,
        generation=generation,
        translation=translation,
        visual_assessment=assessment,
    )

    assert receipt["status"] == "PASSED"
