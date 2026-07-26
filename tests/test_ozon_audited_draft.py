from __future__ import annotations

import pytest

from modules.ozon.catalog_draft import russian_piece_label
from modules.ozon.listing_text import sanitize_ozon_title


@pytest.mark.parametrize(
    ("quantity", "expected"),
    [
        (1, "1 штука"),
        (2, "2 штуки"),
        (5, "5 штук"),
        (11, "11 штук"),
        (21, "21 штука"),
        (24, "24 штуки"),
    ],
)
def test_russian_piece_label_uses_correct_plural(quantity: int, expected: str) -> None:
    assert russian_piece_label(quantity) == expected


def test_russian_piece_label_rejects_non_positive_quantity() -> None:
    with pytest.raises(ValueError, match="positive"):
        russian_piece_label(0)


def test_ozon_title_preserves_ascii_dimension_separator() -> None:
    title = sanitize_ozon_title(
        "Самоклеящаяся настенная наклейка, 30 x 90 см, 2 штуки",
        len_cm="90",
        wid_cm="30",
    )

    assert "90х30 см" in title
    assert "90 30" not in title
