from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import pytest

from core.static_files import resolve_static_path


@pytest.fixture
def public(tmp_path):
    root = tmp_path / "public"
    (root / "nested").mkdir(parents=True)
    for name in ("app.js", "theme.css", "pixel.png", "space name.js", "plus+name.js", "中文.svg"):
        (root / "nested" / name).write_bytes(b"generated public asset")
    (tmp_path / "sentinel.txt").write_bytes(b"generated parent sentinel")
    return root


@pytest.mark.parametrize("relative", (
    "nested/app.js", "nested/theme.css", "nested/pixel.png", "nested/space name.js",
    "nested/plus+name.js", "nested/中文.svg",
))
def test_existing_nested_assets_decode_once(public, relative):
    assert resolve_static_path(public, quote(relative)) == (public / relative).resolve()
    assert resolve_static_path(public, quote(relative, safe="")) == (public / relative).resolve()


@pytest.mark.parametrize("relative", (
    "", ".", "..", "../sentinel.txt", "nested/../../sentinel.txt", "nested/./app.js",
    "nested//app.js", "nested/", "/sentinel.txt", "\\sentinel.txt",
    "..\\sentinel.txt", "nested\\app.js", "C:sentinel.txt", "C:/sentinel.txt",
    "C:\\sentinel.txt", "//s00-fixture.invalid/share/sentinel.txt",
    "\\\\s00-fixture.invalid\\share\\sentinel.txt", "\\\\?\\C:\\sentinel.txt",
    "\\\\.\\C:\\sentinel.txt", "nested/app.js:stream", ".. /sentinel.txt",
    "nested/app.js.", "nested/app.js ", "nested/app.js\x00", "nested/\x1fapp.js", "nested/\x7fapp.js",
))
def test_unsafe_syntax_is_rejected_before_filesystem_access(public, relative, monkeypatch):
    def unexpected_resolve(*args, **kwargs):
        raise AssertionError("unsafe input reached filesystem resolution")

    monkeypatch.setattr(Path, "resolve", unexpected_resolve)
    assert resolve_static_path(public, relative) is None
    assert resolve_static_path(public, quote(relative, safe="")) is None


@pytest.mark.parametrize("relative", ("%", "%2", "%GG", "%ff", "%c0%af", "%ed%a0%80"))
def test_malformed_url_encoding_is_rejected(public, relative):
    assert resolve_static_path(public, relative) is None


@pytest.mark.parametrize("relative", ("missing.js", "nested", "nested/missing/app.js"))
def test_missing_files_and_directories_are_rejected(public, relative):
    assert resolve_static_path(public, relative) is None


def test_second_decode_is_not_applied(public):
    (public / "%2e%2e").mkdir()
    expected = public / "%2e%2e" / "app.js"
    expected.write_bytes(b"literal encoded directory inside public root")
    assert resolve_static_path(public, "%252e%252e/app.js") == expected.resolve()


def test_nonexistent_root_is_rejected(tmp_path):
    assert resolve_static_path(tmp_path / "missing", "app.js") is None
