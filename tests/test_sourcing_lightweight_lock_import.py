"""Cold lock imports and the existing optional package export contract."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib
import importlib.abc
import importlib.util
import json
from pathlib import Path
import sys
import traceback
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXPORTS = {
    "Drission1688": "modules.sourcing.drission_1688",
    "Playwright1688": "modules.sourcing.playwright_1688",
    "Scraper1688": "modules.sourcing.scrape_1688",
}


@contextmanager
def cold_sourcing():
    """Restore the caller's modules and parent attribute even on import failure."""
    import modules
    previous = {k: v for k, v in sys.modules.items()
                if k == "modules.sourcing" or k.startswith("modules.sourcing.")}
    absent = object()
    previous_attribute = vars(modules).get("sourcing", absent)
    for name in previous:
        del sys.modules[name]
    vars(modules).pop("sourcing", None)
    try:
        yield
    finally:
        for name in list(sys.modules):
            if name == "modules.sourcing" or name.startswith("modules.sourcing."):
                del sys.modules[name]
        sys.modules.update(previous)
        if previous_attribute is absent:
            vars(modules).pop("sourcing", None)
        else:
            modules.sourcing = previous_attribute


class UnnecessaryDependencyImport(BaseException):
    """The old optional catch must not hide an actual forbidden import attempt."""


def test_authority_lock_does_not_import_scraper_or_image_dependencies(tmp_path):
    attempts = []

    class Guard(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if (fullname in EXPORTS.values()
                    or fullname.split(".")[0] in {"PIL", "numpy", "playwright", "DrissionPage"}):
                attempts.append({"module": fullname, "stack": traceback.format_stack(limit=12)})
            if fullname.split(".")[0] in {"PIL", "numpy", "playwright", "DrissionPage"}:
                raise UnnecessaryDependencyImport(fullname)
            return None

    # Dependencies already loaded by some unrelated test must not mask an attempt.
    dependencies = {k: v for k, v in sys.modules.items()
                    if k == "core.resource_download"
                    or k.split(".")[0] in {"PIL", "numpy", "playwright", "DrissionPage"}}
    guard = Guard()
    for name in dependencies:
        del sys.modules[name]
    sys.meta_path.insert(0, guard)
    try:
        with cold_sourcing():
            from shared_platform.immutable_approval_files import authority_lock
            path = tmp_path / "approval.json"
            path.write_bytes(b'{"synthetic":true}\n')
            key = hashlib.sha256(path.name.encode("utf-8")).hexdigest()
            lock_path = tmp_path / f".lingshi-{key[:24]}.lock"
            for _ in range(2):
                with authority_lock(path, root=tmp_path):
                    assert path.read_bytes() == b'{"synthetic":true}\n'
                    assert lock_path.exists()
            assert lock_path.read_bytes() == b""
            assert attempts == []
    finally:
        sys.meta_path.remove(guard)
        sys.modules.update(dependencies)
        (tmp_path / "import-attempts.json").write_text(json.dumps(attempts, indent=2), encoding="utf-8")


@pytest.mark.parametrize("export", list(EXPORTS))
def test_optional_public_exports_preserve_identity_from_import_star_and_cache(export):
    with cold_sourcing():
        modules_by_name = {}
        expected = {}
        for name, module_name in EXPORTS.items():
            module = ModuleType(module_name)
            module.__spec__ = importlib.util.spec_from_loader(module_name, loader=None)
            expected[name] = type(name, (), {})
            setattr(module, name, expected[name])
            sys.modules[module_name] = module
            modules_by_name[name] = module
        package = importlib.import_module("modules.sourcing")
        assert package.__all__ == list(EXPORTS)
        assert getattr(package, export) is expected[export]
        namespace = {}
        exec(f"from modules.sourcing import {export}", namespace)
        assert namespace[export] is expected[export]
        star = {}
        exec("from modules.sourcing import *", star)
        assert {name: star[name] for name in EXPORTS} == expected
        setattr(modules_by_name[export], export, object())
        assert getattr(package, export) is expected[export]


def test_missing_optional_exports_stay_none_and_cached():
    class Missing(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname in EXPORTS.values():
                raise ImportError("controlled unavailable optional implementation")
            return None

    guard = Missing()
    with cold_sourcing():
        sys.meta_path.insert(0, guard)
        try:
            package = importlib.import_module("modules.sourcing")
            for export in EXPORTS:
                assert getattr(package, export) is None
        finally:
            sys.meta_path.remove(guard)
        for export, module_name in EXPORTS.items():
            module = ModuleType(module_name)
            setattr(module, export, object())
            sys.modules[module_name] = module
            assert getattr(package, export) is None


def test_unknown_package_attribute_remains_attribute_error():
    # Seed the three optional implementations so this API test never imports PIL.
    with cold_sourcing():
        for export, module_name in EXPORTS.items():
            module = ModuleType(module_name)
            setattr(module, export, None)
            sys.modules[module_name] = module
        package = importlib.import_module("modules.sourcing")
        with pytest.raises(AttributeError):
            getattr(package, "not_an_export")
