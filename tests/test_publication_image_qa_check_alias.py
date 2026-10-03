from shared_platform.publication_image_qa import build_automated_image_qa
from shared_platform.publication_rounds import canonical_digest


def test_visual_assessment_accepts_documented_type_check_key():
    digest = "sha256:" + "a" * 64
    round1 = {
        "offer_id": "123",
        "snapshot_digest": "sha256:" + "b" * 64,
        "image_plan": {
            "brand_plans": [
                {"generated_assets": [{"role": "cover", "quantity": 1}]}
            ]
        },
    }
    generation = {
        "assets": [
            {
                "brand_id": "livelyhive-sea",
                "role": "cover",
                "status": "COMPLETED",
                "artifact_digest": digest,
            }
        ]
    }
    translation = {
        "generation_identity_digest": "sha256:" + "c" * 64,
        "approved_tasks": [],
        "assets": [],
    }
    assessment = {
        "schema_version": "lingshi-image-qa-assessment/v1",
        "status": "PASSED",
        "offer_id": "123",
        "model": "gpt-4o",
        "artifact_digests": [digest],
        "checks": [
            {"type": code, "status": "PASSED", "evidence": "ok"}
            for code in ("FACTUAL_ALIGNMENT", "OCR_LANGUAGE", "DUPLICATION")
        ],
    }
    assessment["assessment_digest"] = canonical_digest(assessment)

    receipt = build_automated_image_qa(
        round1_snapshot=round1,
        generation=generation,
        translation=translation,
        visual_assessment=assessment,
    )

    assert receipt["status"] == "PASSED"
