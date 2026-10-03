from __future__ import annotations

from io import BytesIO

from PIL import Image

from modules.sourcing import new_product_workbench as workbench


class _ReleaseStore:
    def active_plan_for_product(self, offer_id: str) -> dict:
        return {
            "plan_id": "omnichannel:approved-wallpaper",
            "product_id": offer_id,
            "status": "APPROVED",
        }

    def approved_publication_snapshot(self, *, offer_id: str, plan_id: str) -> dict:
        return {
            "schema_version": "approved-publication-snapshot/v4",
            "offer_id": offer_id,
            "plan_id": plan_id,
            "snapshot_digest": f"sha256:{'b' * 64}",
            "product": {
                "images": [
                    f"https://assets.example/master-{position:02d}.png"
                    for position in range(1, 8)
                ]
            },
            "publication_targets": [
                {"target_label": "miaoshou:COMMON"},
                {"target_label": "tiktok:LH_PH"},
                {"target_label": "tiktok:LH_MY"},
                {"target_label": "tiktok:LH_TH"},
                {"target_label": "tiktok:LH_VN"},
                {"target_label": "tiktok:MX"},
                {"target_label": "tiktok:GB"},
                {"target_label": "shopee:PH"},
                {"target_label": "shopee:MY"},
                {"target_label": "shopee:TH"},
                {"target_label": "shopee:VN"},
                {"target_label": "ozon:RU"},
            ],
        }


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (24, 24), "orange").save(output, format="PNG")
    return output.getvalue()


def test_selected_review_generation_is_paid_but_has_zero_platform_writes(workflow,monkeypatch):
    store,project,kwargs=prepared_review(workflow,monkeypatch)
    result=workbench.generate_localized_image_review(OFFER,**kwargs)
    assert len(FixtureClient.calls)==6
    assert result['review']['platform_writes']==0
    assert result['review']['product_center_mutated'] is False
    assert all(t['generation_receipt']['provider']=='lingshi-media/v1' for t in result['review']['tasks'])


def test_generation_cannot_start_without_explicit_paid_confirmation(workflow,monkeypatch):
    store,project,kwargs=prepared_review(workflow,monkeypatch)
    kwargs['confirm_paid_generation']=False
    with pytest.raises(ValueError,match='explicit paid'):
        workbench.generate_localized_image_review(OFFER,**kwargs)
    assert not FixtureClient.calls


def test_retry_reuses_frozen_translations_instead_of_creating_a_new_job_identity(workflow,monkeypatch):
    store,project,kwargs=prepared_review(workflow,monkeypatch)
    def timeout(self,**params):
        self.record('localized')
        raise TimeoutError('fixture POST outcome unknown')
    monkeypatch.setattr(FixtureClient,'create_media_generation',timeout)
    with pytest.raises(TimeoutError):workbench.generate_localized_image_review(OFFER,**kwargs)
    path=workflow.root/'data/localized_image_reviews'/OFFER/'translation-plan.json'
    raw=path.read_bytes()
    count=len(FixtureClient.calls)
    with pytest.raises(Exception):workbench.generate_localized_image_review(OFFER,**kwargs)
    assert len(FixtureClient.calls)==count==2
    assert path.read_bytes()==raw
    assert workflow.context().summary()['unknown']==1

from test_publication_paid_entry import workflow,FixtureClient,OFFER
import pytest
from paid_workbench_fixture import prepared_review
