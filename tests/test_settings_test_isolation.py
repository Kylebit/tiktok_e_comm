"""Each synthetic test is a new settings process; binding is immutable within it."""

import json

import pytest

from core import config


@pytest.mark.parametrize("marker", ["first-independent-test", "next-independent-test"])
def test_independent_cases_start_without_prior_settings_cache(tmp_path, monkeypatch, marker):
    assert config._cache is None
    assert config._cache_source is None
    assert config._cache_source_stat is None
    assert config._cache_explicit_source is None
    settings = tmp_path / "synthetic-settings.json"
    settings.write_text(json.dumps({"test_marker": marker}), encoding="utf-8")
    monkeypatch.setenv("ORBIT_HIVE_SETTINGS", str(settings))
    assert config.load_settings() == {"test_marker": marker}
    assert config._cache_source == settings.resolve()


def test_settings_source_guard_still_rejects_change_within_one_test(tmp_path, monkeypatch):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text('{"test_marker": "original"}', encoding="utf-8")
    second.write_text('{"test_marker": "changed"}', encoding="utf-8")
    monkeypatch.setenv("ORBIT_HIVE_SETTINGS", str(first))
    assert config.load_settings()["test_marker"] == "original"
    monkeypatch.setenv("ORBIT_HIVE_SETTINGS", str(second))
    with pytest.raises(RuntimeError, match="SETTINGS_CACHE_SOURCE_CHANGED"):
        config.load_settings()
    with pytest.raises(RuntimeError, match="SETTINGS_CACHE_SOURCE_CHANGED"):
        config.settings_base_dir()
    assert config._cache_source == first.resolve()
    assert config._cache == {"test_marker": "original"}
