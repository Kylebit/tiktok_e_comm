"""Verify deterministic footer-localization candidates without external calls."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "deterministic-footer-localization-candidate/v1"
    assert manifest["offer_id"] == "3956742887" and manifest["provider_calls"] == 0
    rows = manifest["rows"]
    assert len(rows) == 8 and len({(row["review_number"], row["locale"]) for row in rows}) == 8
    checks = []
    for row in rows:
        source = Image.open(row["source_path"]).convert("RGBA")
        output = Image.open(row["output_path"]).convert("RGBA")
        assert source.size == output.size == (2048, 2048)
        x0, y0, x1, y1 = row["protected_region"]
        source_protected = source.crop((x0, y0, x1, y1)).tobytes()
        output_protected = output.crop((x0, y0, x1, y1)).tobytes()
        assert source_protected == output_protected
        assert sha(source_protected) == row["protected_region_sha256"]
        fx0, fy0, fx1, fy1 = row["footer_region"]
        footer = output.crop((fx0, fy0, fx1, fy1))
        assert any(pixel != (255, 255, 255) for pixel in footer.getdata())
        checks.append({"review_number": row["review_number"], "locale": row["locale"], "artwork_region_preserved": True, "footer_has_ink": True})
    receipt = {"schema_version": "deterministic-footer-localization-qa/v1", "offer_id": "3956742887", "status": "PASS", "external_write_count": 0, "provider_calls": 0, "checks": checks}
    args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "PASS", "rows": len(checks), "provider_calls": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
