import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.sourcing.image_review_package import create_model_suite_proposal
from modules.sourcing.new_product_workbench import (
    _apply_suite_customization,
    _generated_review_images,
    _safe_image_execution_plan,
    prepare_suite_image_generations,
    propose_content_package_with_vision,
    save_generated_image_decision,
    save_content_package_review,
    start_remaining_image_generation,
)
from scripts.generate_approved_image_shot import english_dimension_label


ROOT = Path(__file__).resolve().parents[1]
NODE = Path(r"C:\Users\Windows11\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe")


class NewProductImageWorkflowTests(unittest.TestCase):
    def test_image_review_ui_uses_one_unified_draggable_miaoshou_board(self):
        html = (ROOT / "web" / "new_product.html").read_text(encoding="utf-8")
        self.assertIn('id="finalImageBoardHost"', html)
        self.assertIn("妙手最终图片栏", html)
        self.assertIn("removeUnifiedImage(this,event)", html)
        self.assertIn("同步图片回妙手", html)
        self.assertIn("重做", html)
        self.assertNotIn('class="imageAction"', html)
        self.assertNotIn('class="generatedMiaoshouAction"', html)
        self.assertNotIn("AI 已生成图片（", html)
        self.assertNotIn("<h3>新增图片需求</h3>", html)
        self.assertNotIn("记录新增图片需求</button>", html)
        self.assertIn("按修改意见只重做需要修改的", html)
        self.assertIn("if (reviseCount > 0)", html)
        self.assertNotIn("按修改意见让 AI 重做整套分镜", html)
        self.assertIn("force_shot_ids: pendingShotIds", html)

    def test_redo_route_forwards_the_requested_single_shot(self):
        server = (ROOT / "modules" / "sourcing" / "new_product_server.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("force_shot_ids = data.get(\"force_shot_ids\") or []", server)
        self.assertIn(
            "force_shot_ids=[str(value) for value in force_shot_ids]",
            server,
        )

    def test_background_fx_refresh_does_not_resave_stale_product_review(self):
        html = (ROOT / "web" / "new_product.html").read_text(encoding="utf-8")
        start = html.index("    function applyLiveFxRates(forceAll) {")
        end = html.index("    function refreshLiveFxRates(force) {", start)
        function_source = html[start:end]
        self.assertIn(
            "if (preview && forceAll) saveReview(false, true, 'fx');",
            function_source,
        )
        self.assertNotIn(
            "if (preview) saveReview(false, true, 'fx');",
            function_source,
        )

    def test_paid_shot_script_can_start_outside_repository_cwd(self):
        script = ROOT / "scripts" / "generate_approved_image_shot.py"
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=tmp,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_unified_content_renderer_executes_with_type_counts(self):
        html = (ROOT / "web" / "new_product.html").read_text(encoding="utf-8")
        start = html.index("    function renderUnifiedContentPackage(content) {")
        end = html.index("    function collectContentPackageReview()", start)
        renderer = html[start:end]
        script = """
function esc(value) { return String(value == null ? '' : value); }
function imgSrc(value) { return value; }
""" + renderer + """
const output = renderUnifiedContentPackage({
  package_found: true,
  collect_box_id: '123',
  source_snapshot: {image_urls: [], identity_reference_urls: []},
  fact_card: {verified: [], inferred: [], unknown_or_forbidden: []},
  suite: {items: [{id:'sc1', type:'scene', selected:true}]},
  suite_customization: {type_counts: {scene: 3}},
  generated_review_images: []
});
if (!output.includes('本次生成') || !output.includes('value="3"')) process.exit(2);
"""
        completed = subprocess.run([str(NODE), "-e", script], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_unified_content_renderer_keeps_unsaved_type_count_draft(self):
        html = (ROOT / "web" / "new_product.html").read_text(encoding="utf-8")
        start = html.index("    function renderUnifiedContentPackage(content) {")
        end = html.index("    function collectContentPackageReview()", start)
        renderer = html[start:end]
        script = """
function esc(value) { return String(value == null ? '' : value); }
function imgSrc(value) { return value; }
function currentContentPackageDraft() {
  return {
    fact_card_approved: true,
    suite_approved: true,
    identity_reference_urls: [],
    primary_identity_url: '',
    suite_customization: {type_counts: {scene: 5}}
  };
}
""" + renderer + """
const output = renderUnifiedContentPackage({
  package_found: true,
  collect_box_id: '123',
  source_snapshot: {image_urls: [], identity_reference_urls: []},
  fact_card: {verified: [], inferred: [], unknown_or_forbidden: []},
  suite: {items: [{id:'sc1', type:'scene', selected:true}]},
  suite_customization: {type_counts: {scene: 3}},
  fact_card_approved: true,
  suite_approved: true,
  generated_review_images: []
});
if (!/data-shot-type="scene"[^>]*value="5"/.test(output)) process.exit(2);
if (!output.includes('contentDraftStatus')) process.exit(3);
if (output.includes('contentPackageNote')) process.exit(4);
"""
        completed = subprocess.run([str(NODE), "-e", script], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_content_package_draft_is_scoped_to_active_product_session(self):
        html = (ROOT / "web" / "new_product.html").read_text(encoding="utf-8")
        start = html.index("    function currentContentPackageDraft() {")
        end = html.index("    function renderSessionBar()", start)
        draft_functions = html[start:end]
        script = """
var activeSessionId = 'session-a';
var sessionsById = {'session-a': {contentPackageDraft: null}};
var preview = {offer_id: '3828811808'};
var persisted = 0;
function persistSessionStore() { persisted += 1; }
function collectContentPackageReview() {
  return {suite_customization: {type_counts: {scene: 5}}};
}
var document = {
  querySelector: function(selector) {
    if (selector === '.content-package') return {};
    return null;
  },
  getElementById: function() { return null; }
};
""" + draft_functions + """
captureContentPackageDraft();
var current = currentContentPackageDraft();
if (!current || current.suite_customization.type_counts.scene !== 5) process.exit(2);
if (sessionsById['session-a'].contentPackageDraft.offerId !== '3828811808') process.exit(3);
if (persisted !== 1) process.exit(4);
preview = {offer_id: 'another-product'};
if (currentContentPackageDraft() !== null) process.exit(5);
"""
        completed = subprocess.run([str(NODE), "-e", script], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_category_scene_variants_are_distinct(self):
        suite = {"items": [{"id": "sc1", "type": "scene", "selected": True, "title": "Old", "focus": "Old"}]}
        result = _apply_suite_customization(
            suite, {"type_counts": {"scene": 3}}, profile_id="wall_decal"
        )
        scenes = [row for row in result["items"] if row["type"] == "scene"]
        self.assertEqual(len(scenes), 3)
        self.assertEqual(len({row["title"] for row in scenes}), 3)
        self.assertEqual(len({row["focus"] for row in scenes}), 3)

    def test_paid_execution_requires_current_ai_storyboard(self):
        package = {
            "collect_box": {"source_title": "Cute dog wall decal"},
            "fact_card": {"verified": []},
            "plan": {"_meta": {"category_profile": "wall_decal"}, "suite": {"items": []}},
        }
        with self.assertRaisesRegex(ValueError, "generate an AI storyboard"):
            _safe_image_execution_plan(
                package,
                suite_customization={"type_counts": {"scene": 1}},
                required_planning_signature="current-signature",
            )

    def test_paid_execution_rejects_stale_ai_storyboard(self):
        package = {
            "collect_box": {"source_title": "Cute dog wall decal"},
            "fact_card": {"verified": []},
            "model_proposal": {
                "planning_source": "ai",
                "planning_signature": "old-signature",
            },
            "plan": {"_meta": {"category_profile": "wall_decal"}, "suite": {"items": []}},
        }
        with self.assertRaisesRegex(ValueError, "stale"):
            _safe_image_execution_plan(
                package,
                suite_customization={"type_counts": {"scene": 1}},
                required_planning_signature="current-signature",
            )

    def test_paid_execution_preserves_ai_composition(self):
        package = {
            "collect_box": {"source_title": "Cute dog wall decal"},
            "fact_card": {"verified": [{"field": "材质", "value": "PVC"}]},
            "model_proposal": {
                "planning_source": "ai",
                "planning_signature": "current-signature",
                "model": "vision-model",
            },
            "plan": {
                "analysis": {"category": "wall decal"},
                "_meta": {"category_profile": "wall_decal"},
                "suite": {
                    "items": [
                        {
                            "id": "sc1",
                            "type": "scene",
                            "title": "AI Reading Nook",
                            "focus": "Use a quiet reading nook with realistic scale.",
                            "operator_title_zh": "AI 阅读角",
                            "operator_focus_zh": "在安静阅读角中按真实比例展示。",
                            "selected": True,
                        }
                    ]
                },
            },
        }
        execution = _safe_image_execution_plan(
            package,
            suite_customization={"type_counts": {"scene": 1}},
            required_planning_signature="current-signature",
        )
        self.assertEqual(execution["suite"]["items"][0]["title"], "AI Reading Nook")
        self.assertEqual(execution["_meta"]["planning_source"], "ai")

    def test_ai_planning_receives_saved_local_constraints(self):
        state = {
            "content_package": {
                "collect_box_id": "123",
                "fact_card_approved": True,
                "planning_scope_approved": True,
                "suite_approved": True,
                "identity_reference_urls": ["https://img.example/product.png"],
                "primary_identity_url": "https://img.example/product.png",
                "suite_customization": {"type_counts": {"scene": 2, "selling_point": 1}},
            }
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "modules.sourcing.new_product_workbench.resolve_offer_key", return_value="123"
        ), patch(
            "modules.sourcing.new_product_workbench.load_state", return_value=state
        ), patch(
            "modules.sourcing.new_product_workbench._content_package_dir", return_value=Path(tmp)
        ), patch(
            "modules.sourcing.image_review_package.create_model_suite_proposal",
            return_value={"vision_model_called": True},
        ) as planner, patch(
            "modules.sourcing.new_product_workbench.save_state"
        ), patch(
            "modules.sourcing.new_product_workbench.content_package_summary",
            return_value={"model_proposal": {"valid": True}},
        ):
            result = propose_content_package_with_vision(
                "123",
                reference_urls=["https://img.example/product.png"],
                storyboard_feedback={"sc1": "改成儿童卧室场景，保持商品比例不变。"},
            )
        self.assertTrue(result["proposal"]["vision_model_called"])
        self.assertEqual(
            planner.call_args.kwargs["suite_request"]["type_counts"],
            {"scene": 2, "selling_point": 1},
        )
        self.assertTrue(planner.call_args.kwargs["planning_signature"])
        self.assertEqual(
            planner.call_args.kwargs["storyboard_feedback"],
            {"sc1": "改成儿童卧室场景，保持商品比例不变。"},
        )
        self.assertFalse(state["content_package"]["suite_approved"])
        self.assertEqual(state["content_package"]["storyboard_reviews"], {})

    def test_partial_storyboard_revision_preserves_approved_shots(self):
        state = {
            "content_package": {
                "collect_box_id": "123",
                "fact_card_approved": True,
                "planning_scope_approved": True,
                "suite_approved": False,
                "identity_reference_urls": ["https://img.example/product.png"],
                "primary_identity_url": "https://img.example/product.png",
                "suite_customization": {
                    "type_counts": {"scene": 1, "size_card": 1}
                },
                "storyboard_reviews": {
                    "sc1": {
                        "decision": "approved",
                        "note": "",
                        "reviewed_at": "before",
                    },
                    "sz1": {
                        "decision": "revise",
                        "note": "增加黑色尺寸线和箭头",
                        "reviewed_at": "before",
                    },
                },
            }
        }
        review_package = {
            "plan": {
                "suite": {
                    "items": [
                        {"id": "sc1", "type": "scene", "selected": True},
                        {"id": "sz1", "type": "size_card", "selected": True},
                    ]
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            (package / "review_package.json").write_text(
                json.dumps(review_package), encoding="utf-8"
            )
            with patch(
                "modules.sourcing.new_product_workbench.resolve_offer_key",
                return_value="123",
            ), patch(
                "modules.sourcing.new_product_workbench.load_state",
                return_value=state,
            ), patch(
                "modules.sourcing.new_product_workbench._content_package_dir",
                return_value=package,
            ), patch(
                "modules.sourcing.image_review_package.create_model_suite_proposal",
                return_value={
                    "vision_model_called": True,
                    "revision_target_ids": ["sz1"],
                },
            ) as planner, patch(
                "modules.sourcing.new_product_workbench.save_state"
            ), patch(
                "modules.sourcing.new_product_workbench.content_package_summary",
                return_value={"model_proposal": {"valid": True}},
            ):
                result = propose_content_package_with_vision(
                    "123",
                    reference_urls=["https://img.example/product.png"],
                    storyboard_feedback={"sz1": "增加黑色尺寸线和箭头"},
                )

        self.assertTrue(result["proposal"]["vision_model_called"])
        self.assertEqual(
            planner.call_args.kwargs["revision_target_ids"], ["sz1"]
        )
        content = state["content_package"]
        self.assertEqual(content["pending_regeneration_shot_ids"], ["sz1"])
        self.assertEqual(
            content["storyboard_reviews"]["sc1"]["decision"], "approved"
        )
        self.assertEqual(
            content["storyboard_reviews"]["sz1"]["decision"], "pending"
        )
        self.assertNotIn("force_regenerate_all", content)

    def test_partial_planner_merge_keeps_non_target_storyboard_unchanged(self):
        old_scene = {
            "id": "sc1",
            "type": "scene",
            "title": "Approved scene",
            "focus": "Keep this exact approved composition.",
            "operator_title_zh": "已通过场景",
            "operator_focus_zh": "保持这个已通过构图。",
            "selected": True,
            "aspect_ratio": "1:1",
        }
        old_size = {
            "id": "sz1",
            "type": "size_card",
            "title": "Old size card",
            "focus": "Old size composition.",
            "operator_title_zh": "旧尺寸图",
            "operator_focus_zh": "旧尺寸构图。",
            "selected": True,
            "aspect_ratio": "1:1",
        }
        package_data = {
            "collect_box": {
                "source_title": "Dog wall decal",
                "image_urls": ["https://img.example/product.png"],
            },
            "fact_card": {"verified": [], "unknown_or_forbidden": []},
            "plan": {
                "analysis": {"subject": "Approved dog decal"},
                "suite": {
                    "summary": "Approved suite",
                    "items": [old_scene, old_size],
                },
            },
            "model_proposal": {"model": "previous"},
        }
        candidate = {
            "analysis": {"subject": "AI tried to change the whole suite"},
            "suite": {
                "summary": "AI replacement suite",
                "items": [
                    {
                        **old_scene,
                        "title": "Unwanted changed scene",
                        "focus": "This change must be discarded.",
                    },
                    {
                        **old_size,
                        "title": "Revised size card",
                        "focus": "Add clear black dimension lines and arrowheads.",
                    },
                ],
            },
            "_meta": {
                "model": "vision-test",
                "usage": {"total_tokens": 100},
                "raw_content": "{}",
            },
        }
        locked = {
            "analysis": candidate["analysis"],
            "suite": candidate["suite"],
            "_policy": {
                "category_profile": "wall_decal",
                "rejected_item_ids": [],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            package_dir = Path(tmp)
            package_path = package_dir / "review_package.json"
            package_path.write_text(
                json.dumps(package_data, ensure_ascii=False), encoding="utf-8"
            )
            with patch(
                "modules.sourcing.image_suite_plan.analyze_and_plan_suite",
                return_value=candidate,
            ), patch(
                "modules.sourcing.image_suite_plan.enforce_category_policy",
                return_value=locked,
            ), patch(
                "modules.sourcing.image_review_package.build_shot_prompts",
                return_value={"shots": []},
            ), patch(
                "modules.sourcing.image_review_package.save_shot_prompts"
            ), patch(
                "modules.sourcing.image_review_package._render_report",
                return_value="<html></html>",
            ):
                result = create_model_suite_proposal(
                    package_dir,
                    ["https://img.example/product.png"],
                    suite_request={
                        "type_counts": {"scene": 1, "size_card": 1},
                        "size_card": {
                            "enabled": True,
                            "confirmed": True,
                            "dimensions": "L 34 cm W 58 cm",
                        },
                    },
                    planning_signature="recipe",
                    storyboard_feedback={
                        "sz1": "增加黑色尺寸线和箭头"
                    },
                    revision_target_ids=["sz1"],
                )

            saved = json.loads(package_path.read_text(encoding="utf-8"))

        items = {
            row["id"]: row for row in saved["plan"]["suite"]["items"]
        }
        self.assertEqual(
            items["sc1"]["focus"], "Keep this exact approved composition."
        )
        self.assertEqual(
            items["sz1"]["focus"],
            "Add clear black dimension lines and arrowheads.",
        )
        self.assertEqual(result["revision_target_ids"], ["sz1"])
        self.assertEqual(result["unchanged_item_ids"], ["sc1"])

    def test_size_number_feedback_is_satisfied_locally_without_ai_retry(self):
        state = {
            "content_package": {
                "collect_box_id": "123",
                "fact_card_approved": True,
                "planning_scope_approved": True,
                "suite_approved": False,
                "identity_reference_urls": ["https://img.example/product.png"],
                "primary_identity_url": "https://img.example/product.png",
                "suite_customization": {
                    "type_counts": {"size_card": 1},
                    "size_card": {
                        "enabled": True,
                        "confirmed": True,
                        "dimensions": "长34cm 宽58cm",
                    },
                },
            }
        }
        review_package = {
            "plan": {
                "suite": {
                    "items": [
                        {
                            "id": "sz1",
                            "type": "size_card",
                            "selected": True,
                            "human_dimensions": "长34cm 宽58cm",
                        }
                    ]
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            (package / "review_package.json").write_text(
                json.dumps(review_package), encoding="utf-8"
            )
            with patch(
                "modules.sourcing.new_product_workbench.resolve_offer_key",
                return_value="123",
            ), patch(
                "modules.sourcing.new_product_workbench.load_state",
                return_value=state,
            ), patch(
                "modules.sourcing.new_product_workbench._content_package_dir",
                return_value=package,
            ), patch(
                "modules.sourcing.image_review_package.create_model_suite_proposal"
            ) as planner, patch(
                "modules.sourcing.new_product_workbench.content_package_summary",
                return_value={"suite": {"items": []}},
            ):
                result = propose_content_package_with_vision(
                    "123",
                    reference_urls=["https://img.example/product.png"],
                    storyboard_feedback={"sz1": "图像中需要有数字"},
                )
        planner.assert_not_called()
        self.assertFalse(result["proposal"]["vision_model_called"])
        self.assertEqual(
            result["proposal"]["final_overlay_label"],
            "L 34 cm  |  W 58 cm",
        )

    def test_storyboard_review_requires_every_ai_shot_to_pass(self):
        state = {
            "content_package": {
                "collect_box_id": "123",
                "fact_card_approved": True,
                "planning_scope_approved": True,
                "suite_approved": False,
                "suite_revision": 1,
            }
        }
        review_package = {
            "collect_box": {"image_urls": []},
            "plan": {
                "suite": {
                    "items": [
                        {"id": "sc1", "selected": True},
                        {"id": "sz1", "selected": True},
                    ]
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            (package / "review_package.json").write_text(
                json.dumps(review_package), encoding="utf-8"
            )
            with patch(
                "modules.sourcing.new_product_workbench.resolve_offer_key",
                return_value="123",
            ), patch(
                "modules.sourcing.new_product_workbench.load_state",
                return_value=state,
            ), patch(
                "modules.sourcing.new_product_workbench._content_package_dir",
                return_value=package,
            ), patch(
                "modules.sourcing.new_product_workbench.save_state"
            ), patch(
                "modules.sourcing.new_product_workbench.content_package_summary",
                side_effect=lambda _offer: dict(state["content_package"]),
            ):
                save_content_package_review(
                    "123",
                    {
                        "storyboard_reviews": {
                            "sc1": {"decision": "approved", "note": ""},
                            "sz1": {
                                "decision": "revise",
                                "note": "尺寸留白不足，请增加边距。",
                            },
                        }
                    },
                )
                self.assertFalse(state["content_package"]["suite_approved"])
                self.assertEqual(
                    state["content_package"]["storyboard_reviews"]["sz1"]["decision"],
                    "revise",
                )
                save_content_package_review(
                    "123",
                    {
                        "storyboard_reviews": {
                            "sc1": {"decision": "approved", "note": ""},
                            "sz1": {"decision": "approved", "note": ""},
                        }
                    },
                )
        self.assertTrue(state["content_package"]["suite_approved"])

    def test_generated_review_shows_only_latest_verified_version_per_shot(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            generated = package / "generated"
            generated.mkdir()
            for artifact_id, created in (("sc1_r1_1", "2026-01-01"), ("sc1_r2_2", "2026-01-02")):
                (generated / f"{artifact_id}.png").write_bytes(b"png")
                (package / f"generation_audit_{artifact_id}.json").write_text(json.dumps({
                    "shot_id": "sc1", "created_at": created, "download_verified": True,
                    "final_response": {"result": {"data": [{"url": f"https://img.example/{artifact_id}.png"}]}},
                }), encoding="utf-8")
            old = package / "generation_audit_sc1_r1_1.json"
            old.touch()
            latest = package / "generation_audit_sc1_r2_2.json"
            latest.touch()
            rows = _generated_review_images("123", {}, package)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["artifact_id"], "sc1_r2_2")
        self.assertEqual(rows[0]["version_count"], 2)

    def test_recipe_change_invalidates_old_paid_preflight(self):
        state = {"content_package": {
            "fact_card_approved": True,
            "suite_approved": True,
            "suite_revision": 2,
            "suite_customization": {"type_counts": {"scene": 1}},
            "remaining_images_preflight": {"status": "ready_for_explicit_paid_confirmation", "shots": [{"id": "sc1"}]},
            "remaining_images_generation": {"status": "completed_waiting_human_review", "items": []},
        }}
        with patch("modules.sourcing.new_product_workbench.resolve_offer_key", return_value="123"), patch(
            "modules.sourcing.new_product_workbench.load_state", return_value=state
        ), patch("modules.sourcing.new_product_workbench.save_state"), patch(
            "modules.sourcing.new_product_workbench.content_package_summary", return_value=state["content_package"]
        ):
            result = save_content_package_review("123", {
                "fact_card_approved": True,
                "suite_approved": True,
                "suite_customization": {"type_counts": {"scene": 3}},
            })
        self.assertNotIn("remaining_images_preflight", result)
        self.assertNotIn("remaining_images_generation", result)
        self.assertEqual(result["suite_revision"], 3)
        self.assertTrue(result["force_regenerate_all"])

    def test_paid_start_rejects_stale_recipe_revision(self):
        state = {"content_package": {
            "suite_revision": 3,
            "remaining_images_preflight": {
                "status": "ready_for_explicit_paid_confirmation",
                "suite_revision": 2,
                "recipe_signature": "old",
                "shots": [{"id": "sc1"}],
            },
        }}
        with patch("modules.sourcing.new_product_workbench.resolve_offer_key", return_value="stale-revision-case"), patch(
            "modules.sourcing.new_product_workbench.load_state", return_value=state
        ):
            with self.assertRaisesRegex(ValueError, "recipe changed"):
                start_remaining_image_generation("stale-revision-case")

    def test_failed_only_retry_does_not_queue_successful_shots(self):
        state = {"content_package": {
            "suite_revision": 4,
            "remaining_images_preflight": {
                "status": "ready_for_explicit_paid_confirmation",
                "suite_revision": 4,
                "recipe_signature": "same",
                "shots": [
                    {"id": "sc1", "artifact_id": "sc1_r4"},
                    {"id": "sz1", "artifact_id": "sz1_r4"},
                ],
            },
            "remaining_images_generation": {
                "status": "completed_with_errors",
                "items": [
                    {"shot_id": "sc1", "artifact_id": "sc1_r4", "status": "completed_waiting_human_review"},
                    {"shot_id": "sz1", "artifact_id": "sz1_r4", "status": "failed"},
                ],
            },
        }}
        with patch("modules.sourcing.new_product_workbench.resolve_offer_key", return_value="retry-case"), patch(
            "modules.sourcing.new_product_workbench.load_state", return_value=state
        ), patch(
            "modules.sourcing.new_product_workbench._content_recipe_signature", return_value="same"
        ), patch(
            "modules.sourcing.new_product_workbench.save_state"
        ), patch(
            "modules.sourcing.new_product_workbench.content_package_summary", return_value={}
        ), patch(
            "modules.sourcing.new_product_workbench.threading.Thread"
        ) as thread_mock:
            result = start_remaining_image_generation("retry-case", retry_failed_only=True)

        self.assertTrue(result["ok"])
        queued = state["content_package"]["remaining_images_generation"]["items"]
        self.assertEqual([row["shot_id"] for row in queued], ["sz1"])
        self.assertTrue(state["content_package"]["remaining_images_generation"]["retry_failed_only"])
        self.assertEqual(len(state["content_package"]["image_generation_history"]), 1)
        thread_mock.return_value.start.assert_called_once()

    def test_pending_storyboard_revision_only_prepares_targeted_nonpaid_preflight(self):
        state = {"content_package": {
            "fact_card_approved": True,
            "suite_approved": True,
            "suite_revision": 4,
            "collect_box_id": "123",
            "identity_reference_urls": ["https://img.example/reference.png"],
            "pending_regeneration_shot_ids": ["sc1"],
        }}
        execution_plan = {
            "_meta": {"image_url": "https://img.example/reference.png"},
            "suite": {"items": []},
        }
        prompt_bundle = {
            "shots": [
                {
                    "id": "sc1",
                    "type": "scene",
                    "title": "Scene",
                    "focus": "Focus",
                    "aspect_ratio": "1:1",
                    "prompt": "Generate scene",
                },
                {
                    "id": "sp1",
                    "type": "selling_point",
                    "title": "Selling point",
                    "focus": "Focus",
                    "aspect_ratio": "1:1",
                    "prompt": "Generate selling point",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "modules.sourcing.new_product_workbench.resolve_offer_key", return_value="123"
        ), patch(
            "modules.sourcing.new_product_workbench.load_state", return_value=state
        ), patch(
            "modules.sourcing.new_product_workbench._content_package_dir", return_value=Path(tmp)
        ), patch(
            "modules.sourcing.new_product_workbench._content_artifacts",
            return_value=[
                {"shot_id": "sc1", "technical_complete": True},
                {"shot_id": "sp1", "technical_complete": True},
            ],
        ), patch(
            "modules.sourcing.new_product_workbench._load_json", return_value={}
        ), patch(
            "modules.sourcing.new_product_workbench._safe_image_execution_plan",
            return_value=execution_plan,
        ), patch(
            "modules.sourcing.image_shot_prompts.build_shot_prompts",
            return_value=prompt_bundle,
        ), patch(
            "modules.sourcing.toapis_client.build_generation_payload",
            return_value={"model": "gpt-image-2"},
        ) as payload_mock, patch(
            "modules.sourcing.new_product_workbench._content_recipe_signature",
            return_value="recipe-v4",
        ), patch(
            "modules.sourcing.new_product_workbench.save_state"
        ) as save_mock, patch(
            "modules.sourcing.new_product_workbench.content_package_summary",
            return_value={},
        ):
            result = prepare_suite_image_generations("123")

        self.assertTrue(result["ok"])
        self.assertEqual(result["preflight"]["status"], "ready_for_explicit_paid_confirmation")
        self.assertEqual([row["id"] for row in result["preflight"]["shots"]], ["sc1"])
        self.assertTrue(result["preflight"]["targeted_regeneration"])
        self.assertNotIn("remaining_images_generation", state["content_package"])
        payload_mock.assert_called_once()
        save_mock.assert_called_once()

    def test_chinese_dimension_input_becomes_english_overlay_copy(self):
        self.assertEqual(english_dimension_label("长34cm 宽58cm"), "L 34 cm  |  W 58 cm")

    def test_generated_image_decision_saves_locally_without_miaoshou_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            (package / "generated").mkdir()
            (package / "generated" / "sc1_v1.png").write_bytes(b"png")
            (package / "generation_audit_sc1_v1.json").write_text(json.dumps({
                "download_verified": True,
                "final_response": {"result": {"data": [{"url": "https://img.example/sc1.png"}]}},
            }), encoding="utf-8")
            state = {"content_package": {"collect_box_id": "123"}}
            with patch("modules.sourcing.new_product_workbench.resolve_offer_key", return_value="123"), patch(
                "modules.sourcing.new_product_workbench.load_state", return_value=state
            ), patch(
                "modules.sourcing.new_product_workbench._content_package_dir", return_value=package
            ), patch("modules.sourcing.new_product_workbench.save_state") as save_mock, patch(
                "modules.sourcing.new_product_workbench.content_package_summary", return_value={}
            ):
                result = save_generated_image_decision("123", "sc1_v1", "keep")

        self.assertTrue(result["ok"])
        self.assertFalse(result["written_to_miaoshou"])
        self.assertEqual(state["content_package"]["generated_image_miaoshou_decisions"]["sc1_v1"]["action"], "keep")
        self.assertEqual(state["review"]["image_order"], ["https://img.example/sc1.png"])
        save_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
