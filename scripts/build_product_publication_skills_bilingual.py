from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "PRODUCT_PUBLICATION_SKILLS_EN_ZH.md"
SKILLS = (
    "prepare-product-publication",
    "prepare-product-images",
    "publish-approved-product",
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build() -> str:
    rows: list[tuple[str, str, str, str]] = []
    for name in SKILLS:
        source_path = ROOT / "skills" / name / "SKILL.md"
        translation_path = ROOT / "docs" / "skill-translations" / f"{name}.zh-CN.md"
        source = source_path.read_text(encoding="utf-8")
        translation = translation_path.read_text(encoding="utf-8")
        digest = _digest(source_path.read_bytes())
        marker = f"<!-- source_sha256: {digest} -->"
        if marker not in translation:
            raise RuntimeError(
                f"stale Chinese translation for {name}: expected {marker}"
            )
        rows.append((name, digest, source.rstrip(), translation.rstrip()))

    output = [
        "# 商品发布 Skills 中英对照版",
        "",
        "> **非执行权威。** 本文件仅供 Kyle 阅读。真实执行只使用仓库中的三份英文 `SKILL.md`；英文 `SKILL.md` 是唯一执行权威。本文件由构建脚本机械嵌入英文原文并附上人工维护的完整中文翻译，不包含 Skill frontmatter，也不会被 Skill 系统加载。",
        "",
        "同步契约：`product-publication-skills-bilingual/v1`。任一英文源文件变化后，如果对应中文翻译未更新源 SHA-256，构建与测试都会失败。",
        "",
        "| Skill | 英文执行权威 | 中文翻译源 | SHA-256 |",
        "|---|---|---|---|",
    ]
    for name, digest, _source, _translation in rows:
        output.append(
            f"| `{name}` | `skills/{name}/SKILL.md` | "
            f"`skill-translations/{name}.zh-CN.md` | `{digest}` |"
        )

    for index, (name, digest, source, translation) in enumerate(rows, start=1):
        output.extend(
            [
                "",
                "---",
                "",
                f"# {index}. `{name}`",
                "",
                f"源 SHA-256：`{digest}`",
                "",
                "## English source (verbatim)",
                "",
                "````markdown",
                source,
                "````",
                "",
                "## 中文完整翻译",
                "",
                translation,
            ]
        )
    output.append("")
    return "\n".join(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = build()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != expected:
            raise SystemExit("bilingual Skill document is stale; rebuild it")
        print("bilingual Skill document is synchronized")
        return 0
    OUTPUT.write_text(expected, encoding="utf-8", newline="\n")
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
