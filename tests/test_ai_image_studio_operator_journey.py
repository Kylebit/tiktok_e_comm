from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import tempfile

from modules.sourcing import image_review_package
from modules.sourcing import new_product_workbench as workbench


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web/ai_image_studio.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "web/static/ai_image_studio.js").read_text(encoding="utf-8")
NODE = Path(
    r"C:\Users\Windows11\.cache\codex-runtimes\codex-primary-runtime"
    r"\dependencies\node\bin\node.exe"
)


def _run_node(source: str) -> None:
    with tempfile.TemporaryDirectory() as directory:
        script = Path(directory) / "operator_journey.js"
        script.write_text(source, encoding="utf-8")
        completed = subprocess.run(
            [str(NODE), str(script)],
            capture_output=True,
            text=True,
            timeout=20,
        )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def _studio_prefix(end_marker: str) -> str:
    """Return studio declarations/functions without binding page events."""
    start = SCRIPT.index('"use strict";')
    end = SCRIPT.index(end_marker, start)
    return SCRIPT[start:end]


def _dom_harness() -> str:
    return r"""
const nodeByKey = {};
function fakeNode(key) {
  if (!nodeByKey[key]) {
    nodeByKey[key] = {
      key,
      value: "",
      checked: false,
      hidden: false,
      disabled: false,
      textContent: "",
      innerHTML: "",
      href: "",
      dataset: {},
      classList: {
        add() {},
        remove() {},
        toggle() {},
      },
      addEventListener() {},
      removeAttribute(name) {
        if (name === "href") this.href = "";
      },
      setAttribute(name, value) {
        this[name] = String(value);
      },
    };
  }
  return nodeByKey[key];
}
const recipeNodes = {
  scene: fakeNode("recipe:scene"),
  selling_point: fakeNode("recipe:selling_point"),
  size_card: fakeNode("recipe:size_card"),
};
for (const [type, node] of Object.entries(recipeNodes)) {
  node.dataset.recipeType = type;
}
const strategyNodes = [
  Object.assign(fakeNode("strategy:source"), {value: "source_only"}),
  Object.assign(fakeNode("strategy:ai"), {value: "ai_assisted"}),
];
const document = {
  querySelector(selector) {
    const recipe = selector.match(
      /^\.recipe-count\[data-recipe-type="([^"]+)"\]$/
    );
    if (recipe) return recipeNodes[recipe[1]] || null;
    if (selector === ".identity-primary:checked") return null;
    return fakeNode(selector);
  },
  querySelectorAll(selector) {
    if (selector === ".recipe-count") return Object.values(recipeNodes);
    if (selector === 'input[name="contentStrategy"]') return strategyNodes;
    if ([
      ".identity-reference:checked",
      ".story-decision",
      ".asset-decision",
      ".final-action",
    ].includes(selector)) return [];
    return [];
  },
};
"""


def test_offer_card_links_to_miaoshou_and_the_exact_collected_1688_source():
    assert 'id="projectSourceLinks"' in HTML
    assert 'id="miaoshouCollectLink"' in SCRIPT
    assert 'id="source1688Link"' in SCRIPT
    assert 'target="_blank"' in SCRIPT
    assert 'rel="noopener"' in SCRIPT

    source = _dom_harness() + _studio_prefix("  function renderSources()") + r"""
function schedulePoll() {}
preview = {
  offer_id: "3828540231",
  review: {
    title: "Wall decal",
    image_actions: [],
  },
  source: {
    source_url: "https://detail.1688.com/offer/1018142152850.html",
    skus: [],
  },
  content_package: {
    collect_box_id: "3828540231",
    generated_review_images: [],
  },
  workflow: {},
};
finalOrder = [];
renderProject();
const links = nodeByKey["#projectSourceLinks"].innerHTML;
if (!links.includes('id="miaoshouCollectLink"')) process.exit(2);
if (!links.includes('href="https://erp.91miaoshou.com/"')) process.exit(3);
if (!links.includes("3828540231")) process.exit(4);
if (!links.includes('id="source1688Link"')) process.exit(5);
if (!links.includes(
  'href="https://detail.1688.com/offer/1018142152850.html"'
)) process.exit(6);
if (!links.includes("1018142152850")) process.exit(7);
"""
    _run_node(source)


def test_ai_storyboard_becomes_generation_ready_without_per_card_approval(
    tmp_path,
    monkeypatch,
):
    reference = "https://assets.example/identity.jpg"
    state = {
        "offer_id": "3828540231",
        "_revision": 3,
        "review": {},
        "content_package": {
            "content_strategy": "ai_assisted",
            "collect_box_id": "3828540231",
            "fact_card_approved": True,
            "planning_scope_approved": True,
            "suite_approved": False,
            "identity_reference_urls": [reference],
            "suite_customization": {
                "type_counts": {
                    "scene": 1,
                    "selling_point": 1,
                    "size_card": 0,
                },
                "size_card": {
                    "enabled": False,
                    "dimensions": "",
                    "confirmed": False,
                },
            },
        },
    }
    package_dir = tmp_path / "3828540231"
    package_dir.mkdir()
    package_path = package_dir / "review_package.json"
    package_path.write_text(
        json.dumps(
            {
                "collect_box": {
                    "detail_id": 3828540231,
                    "image_urls": [reference],
                    "primary_identity_image": reference,
                },
                "plan": {"suite": {"items": []}},
            }
        ),
        encoding="utf-8",
    )

    def load_state(_offer_id: str) -> dict:
        return copy.deepcopy(state)

    def save_state(_offer_id: str, next_state: dict) -> dict:
        state.clear()
        state.update(copy.deepcopy(next_state))
        state["_revision"] = int(state.get("_revision") or 0) + 1
        return copy.deepcopy(state)

    def create_proposal(
        output_dir: Path,
        _refs: list[str],
        *,
        planning_signature: str,
        **_kwargs,
    ) -> dict:
        package = json.loads(package_path.read_text(encoding="utf-8"))
        package["model_proposal"] = {
            "planning_source": "ai",
            "planning_signature": planning_signature,
            "usage": {},
        }
        package["plan"] = {
            "_meta": {"category_profile": "wall_decal"},
            "suite": {
                "items": [
                    {
                        "id": "sc1",
                        "type": "scene",
                        "selected": True,
                        "ai_planned": True,
                    },
                    {
                        "id": "sp1",
                        "type": "selling_point",
                        "selected": True,
                        "ai_planned": True,
                    },
                ]
            },
        }
        (output_dir / "review_package.json").write_text(
            json.dumps(package),
            encoding="utf-8",
        )
        return {"vision_model_called": True}

    monkeypatch.setattr(workbench, "resolve_offer_key", lambda _value: "3828540231")
    monkeypatch.setattr(workbench, "load_state", load_state)
    monkeypatch.setattr(workbench, "save_state", save_state)
    monkeypatch.setattr(workbench, "_content_package_dir", lambda _value: package_dir)
    monkeypatch.setattr(
        image_review_package,
        "create_model_suite_proposal",
        create_proposal,
    )

    result = workbench.propose_content_package_with_vision(
        "3828540231",
        reference_urls=[reference],
    )

    assert result["proposal"]["vision_model_called"] is True
    assert state["content_package"]["suite_approved"] is True
    auto_adopted = state["content_package"].get("storyboard_reviews") or {}
    assert set(auto_adopted) == {"sc1", "sp1"}
    assert {
        row["decision"] for row in auto_adopted.values()
    } == {"auto_adopted"}
    assert {
        row["review_source"] for row in auto_adopted.values()
    } == {"experience_recipe_auto_v1"}
    assert result["content_package"]["suite_approved"] is True
    assert result["content_package"]["model_proposal"]["valid"] is True


def test_storyboard_cards_are_informational_but_generated_images_keep_human_review():
    storyboard_start = SCRIPT.index("  function renderStoryboard()")
    storyboard_end = SCRIPT.index("  function updateStrategyUi()", storyboard_start)
    storyboard = SCRIPT[storyboard_start:storyboard_end]
    versions_start = SCRIPT.index("  function renderVersions()")
    versions_end = SCRIPT.index("  function renderFinal()", versions_start)
    versions = SCRIPT[versions_start:versions_end]

    assert "story-decision" not in storyboard
    assert "story-note" not in storyboard
    assert "无需逐卡审批" in storyboard
    assert "asset-decision" in versions
    assert '"pending", "approved", "rework", "rejected"' in versions
    assert "版本审核" in versions
    assert 'id="saveVersionsButton"' in HTML


def test_quiet_poll_preserves_an_unsaved_recipe_then_saved_server_value_wins():
    prefix = _studio_prefix("  async function preparePackage()")
    source = _dom_harness() + prefix + r"""
function schedulePoll() {}
preview = {
  offer_id: "3828540231",
  review: {image_actions: [], image_order: []},
  source: {},
  content_package: {
    content_strategy: "ai_assisted",
    package_found: true,
    fact_card_approved: true,
    planning_scope_approved: true,
    suite_customization: {
      type_counts: {scene: 1, selling_point: 1, size_card: 0},
      size_card: {enabled: false, dimensions: "", confirmed: false},
    },
    suite: {
      items: [
        {id: "sc1", type: "scene", selected: true, title: "Scene"},
        {id: "sp1", type: "selling_point", selected: true, title: "Point"},
      ],
    },
  },
};
renderStoryboard();
if (recipeNodes.scene.value !== "1") process.exit(2);

recipeNodes.scene.value = "5";
recipeNodes.scene.oninput();

// A quiet generation-status poll returned an older saved recipe. Re-rendering
// must not replace the operator's unsaved number.
preview.content_package = {
  ...preview.content_package,
  suite_customization: {
    type_counts: {scene: 2, selling_point: 1, size_card: 0},
    size_card: {enabled: false, dimensions: "", confirmed: false},
  },
};
renderStoryboard();
if (recipeNodes.scene.value !== "5") process.exit(3);

let postedReview = null;
post = async function (_suffix, payload) {
  postedReview = payload.review;
  // The server remains authoritative and may normalize the submitted value.
  return {
    content_package: {
      ...preview.content_package,
      suite_customization: {
        type_counts: {scene: 4, selling_point: 1, size_card: 0},
        size_card: {enabled: false, dimensions: "", confirmed: false},
      },
    },
  };
};

(async function () {
  await saveContentReview({quiet: true});
  if (postedReview.suite_customization.type_counts.scene !== 5) process.exit(4);
  renderStoryboard();
  if (recipeNodes.scene.value !== "4") process.exit(5);
})().catch((error) => {
  process.stderr.write(String(error && error.stack || error));
  process.exit(6);
});
"""
    _run_node(source)
