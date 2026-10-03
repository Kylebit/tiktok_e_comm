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
    rows: list[tuple[str, str, str, str, bool]] = []
    for name in SKILLS:
        source_path = ROOT / "skills" / name / "SKILL.md"
        translation_path = ROOT / "docs" / "skill-translations" / f"{name}.zh-CN.md"
        source = source_path.read_text(encoding="utf-8")
        translation = translation_path.read_text(encoding="utf-8")
        digest = _digest(source_path.read_bytes())
        marker = f"<!-- source_sha256: {digest} -->"
        current = marker in translation
        if not current and not (
            "历史译本" in translation and "当前英文 `SKILL.md`" in translation
        ):
            raise RuntimeError(
                f"unmarked stale Chinese translation for {name}: expected {marker}"
            )
        rows.append((name, digest, source.rstrip(), translation.rstrip(), current))

    output = [
        "# 商品发布 Skills 中英对照版",
        "",
        "> **非执行权威。** 本文件仅供 Kyle 阅读。真实执行只使用仓库中的三份英文 `SKILL.md`；英文 `SKILL.md` 是唯一执行权威。本文件由构建脚本机械嵌入当前英文原文和人工维护的中文参考译文，不包含 Skill frontmatter，也不会被 Skill 系统加载。过期译文仅供追溯，不能指导当前执行。",
        "",
        "同步契约：`product-publication-skills-bilingual/v1`。任一英文源文件变化后，对应中文译文必须更新并绑定当前源 SHA-256，或明确标记为历史译本；未标记的过期译文会使构建与测试失败。",
        "",
        "| Skill | 英文执行权威 | 中文翻译源 | 英文 SHA-256 | 译文状态 |",
        "|---|---|---|---|---|",
    ]
    for name, digest, _source, _translation, current in rows:
        output.append(
            f"| `{name}` | `skills/{name}/SKILL.md` | "
            f"`skill-translations/{name}.zh-CN.md` | `{digest}` | "
            f"{'已同步' if current else '历史译本，勿用于执行'} |"
        )

    for index, (name, digest, source, translation, current) in enumerate(rows, start=1):
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
                "## 中文参考译文",
                "",
                (f"`{name}` 中文译文已同步。" if current else
                 f"**`{name}` 中文译文已过期：以下为历史译本，不得据此执行；请以本节当前英文 `SKILL.md` 原文为准。**"),
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
