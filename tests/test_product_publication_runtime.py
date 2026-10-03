from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "product_publication_runtime.py"


def _module():
    spec = importlib.util.spec_from_file_location("product_publication_runtime", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_service_contract_uses_exact_commands_and_identity_health_endpoints():
    runtime = _module()
    assert str(runtime.ROOT) in runtime.sys.path
    specs = runtime.service_specs(root=ROOT, executable=Path("python"))

    assert [(item.name, item.port, item.health_url) for item in specs] == [
        ("product-center", 8765, "http://127.0.0.1:8765/api/health"),
        ("new-product-workbench", 8766, "http://127.0.0.1:8766/health"),
    ]
    assert specs[0].command == (
        "python",
        str(ROOT / "main.py"),
        "serve",
        "--port",
        "8765",
        "--page",
        "product",
        "--no-browser",
    )
    assert specs[1].command == (
        "python",
        str(ROOT / "scripts" / "start_new_product_server.py"),
        "8766",
    )


def test_status_is_read_only_and_validates_service_identity():
    runtime = _module()
    specs = runtime.service_specs(root=ROOT, executable=Path("python"))
    payloads = {
        specs[0].health_url: {"ok": True, "service": "orbit-hive-local-console"},
        specs[1].health_url: {"ok": True, "service": "something-else"},
    }
    launched = []

    def probe(spec):
        return runtime.probe_service(
            spec,
            fetch_json=lambda url, _timeout: payloads[url],
            port_check=lambda _host, _port, _timeout: True,
        )

    result = runtime.runtime_status(specs=specs, probe=probe)

    assert launched == []
    assert result["ok"] is False
    assert result["services"]["product-center"]["state"] == "UNKNOWN"
    assert result["services"]["new-product-workbench"]["state"] == "WRONG_SERVICE"
    assert "something-else" not in json.dumps(result)


def test_start_reuses_healthy_service_and_launches_only_stopped_service():
    runtime = _module()
    specs = runtime.service_specs(root=ROOT, executable=Path("python"))
    launched = []
    calls = {"new-product-workbench": 0}

    def probe(spec):
        if spec.name == "product-center":
            return runtime.service_result(spec, "READY")
        calls[spec.name] += 1
        state = "STOPPED" if calls[spec.name] == 1 else "READY"
        return runtime.service_result(spec, state)

    class Process:
        pid = 4321

    result = runtime.start_runtime(
        specs=specs,
        probe=probe,
        launcher=lambda spec: launched.append(spec.name) or Process(),
        timeout_seconds=1,
        poll_seconds=0,
        sleep=lambda _seconds: None,
    )

    assert result["ok"] is True
    assert launched == ["new-product-workbench"]
    assert result["started"] == {"new-product-workbench": 4321}


def test_start_refuses_occupied_or_wrong_ports_without_launching_anything():
    runtime = _module()
    specs = runtime.service_specs(root=ROOT, executable=Path("python"))
    launched = []

    def probe(spec):
        state = "PORT_IN_USE" if spec.name == "product-center" else "STOPPED"
        return runtime.service_result(spec, state)

    result = runtime.start_runtime(
        specs=specs,
        probe=probe,
        launcher=lambda spec: launched.append(spec.name),
        timeout_seconds=0,
    )

    assert result["ok"] is False
    assert result["error"] == "runtime port conflict; no services were started"
    assert launched == []


def test_launcher_writes_logs_and_atomic_pid_record_without_running_live_process(
    tmp_path, monkeypatch
):
    runtime = _module()
    spec = runtime.service_specs(root=ROOT, executable=Path("python"))[0]
    observed = {}

    class Process:
        pid = 9876

    def fake_popen(command, **kwargs):
        observed["command"] = tuple(command)
        observed.update(kwargs)
        return Process()

    monkeypatch.setattr(runtime.subprocess, "Popen", fake_popen)

    process = runtime.launch_service(spec, runtime_dir=tmp_path)

    assert process.pid == 9876
    assert observed["command"] == spec.command
    assert observed["cwd"] == str(ROOT)
    record = json.loads((tmp_path / "product-center.pid.json").read_text(encoding="utf-8"))
    assert record["pid"] == 9876
    assert record["service"] == "product-center"
    assert "command" not in record
    assert (tmp_path / "product-center.stdout.log").is_file()
    assert (tmp_path / "product-center.stderr.log").is_file()


def test_takeover_check_is_read_only_and_classifies_each_gate(tmp_path):
    runtime = _module()
    entry = tmp_path / "skills" / "publish-approved-product" / "scripts" / "product_center_publication.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("# entry\n", encoding="utf-8")
    for name in ("prepare-product-publication", "prepare-product-images"):
        skill = tmp_path / "skills" / name / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: x\n---\n", encoding="utf-8")

    tracked = tuple(
        path.relative_to(tmp_path).as_posix()
        for path in (tmp_path / "skills").rglob("*")
        if path.is_file()
    )
    result = runtime.takeover_check(
        repository=tmp_path,
        runtime_probe=lambda: {"ok": True, "services": {}},
        parity_check=lambda: {"ok": True, "suite_digest": "digest"},
        tracked_files=lambda _repo: tracked,
    )

    assert result == {
        "ok": True,
        "checks": {
            "runtime": True,
            "skill_parity": True,
            "canonical_skills_tracked": True,
            "publish_entry": True,
        },
        "runtime": {"ok": True, "services": {}},
        "skill_suite_digest": "digest",
        "untracked_canonical_files": [],
        "publish_entry": "skills/publish-approved-product/scripts/product_center_publication.py",
    }


def test_takeover_check_reports_untracked_skill_without_starting_or_posting(tmp_path):
    runtime = _module()
    skill = tmp_path / "skills" / "prepare-product-publication" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: x\n---\n", encoding="utf-8")

    result = runtime.takeover_check(
        repository=tmp_path,
        runtime_probe=lambda: {"ok": False, "services": {}},
        parity_check=lambda: {"ok": False, "suite_digest": "digest"},
        tracked_files=lambda _repo: (),
    )

    assert result["ok"] is False
    assert result["checks"] == {
        "runtime": False,
        "skill_parity": False,
        "canonical_skills_tracked": False,
        "publish_entry": False,
    }
    assert result["untracked_canonical_files"] == [
        "skills/prepare-product-publication/SKILL.md"
    ]


def test_formal_takeover_binds_explicit_data_without_runtime_or_profile(tmp_path):
    runtime = _module()
    calls = []

    def bind(root, commit):
        calls.append(("bind", root, commit))
        return {"commit": commit, "execution_authority": "NONE_READ_ONLY_TAKEOVER"}

    def inspect(*, offer_id, data_root):
        calls.append(("inspect", offer_id, data_root))
        return {"status": "READ_ONLY_BOUND", "offer_id": offer_id, "paid_requests": 0, "business_writes": 0}

    result = runtime.takeover_publication(
        expected_commit="a" * 40,
        data_root=tmp_path,
        offer_id="123",
        source_binder=bind,
        inspector=inspect,
    )

    assert calls == [("bind", runtime.ROOT.resolve(), "a" * 40), ("inspect", "123", tmp_path)]
    assert result["source_binding"]["execution_authority"] == "NONE_READ_ONLY_TAKEOVER"
    assert result["runtime_started"] is False
    assert result["runtime_profile_consumed"] is False
    assert result["data_root_selection"] == "EXPLICIT_CLI_ARGUMENT"


def test_formal_takeover_cli_does_not_probe_or_bootstrap_runtime(tmp_path, monkeypatch, capsys):
    runtime = _module()
    seen = []
    monkeypatch.setattr(runtime, "runtime_status", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("runtime probe")))
    monkeypatch.setattr(
        runtime,
        "takeover_publication",
        lambda **kwargs: seen.append(kwargs) or {"status": "READ_ONLY_BOUND", "paid_requests": 0, "business_writes": 0},
    )

    assert runtime.main([
        "--takeover-offer-id", "123", "--expected-commit", "a" * 40, "--data-root", str(tmp_path)
    ]) == 0
    assert seen == [{"expected_commit": "a" * 40, "data_root": tmp_path, "offer_id": "123"}]
    assert json.loads(capsys.readouterr().out)["status"] == "READ_ONLY_BOUND"


def test_formal_takeover_rejects_implicit_profile_or_missing_data_root(tmp_path, capsys):
    runtime = _module()
    assert runtime.main(["--takeover-offer-id", "123", "--expected-commit", "a" * 40]) == 1
    missing = json.loads(capsys.readouterr().out)
    assert missing["status"] == "TAKEOVER_REJECTED"
    assert missing["paid_requests"] == missing["business_writes"] == 0
    assert runtime.main([
        "--takeover-offer-id", "123", "--expected-commit", "a" * 40,
        "--data-root", str(tmp_path), "--profile", str(tmp_path / "runtime-profile.json"),
    ]) == 1
    profile = json.loads(capsys.readouterr().out)
    assert profile["status"] == "TAKEOVER_REJECTED"


def test_runtime_profile_check_is_read_only_and_binds_explicit_settings(tmp_path):
    runtime = _module()
    settings = tmp_path / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    profile = tmp_path / "runtime-profile.json"
    stores = {name: str((tmp_path / "data" / name).resolve()) for name in ("catalog", "reports_release", "workbench", "ozon")}
    profile.write_text(json.dumps({
        "profile_id": "fixture-profile", "settings_path": str(settings.resolve()), "stores": stores,
    }), encoding="utf-8")

    result = runtime.runtime_profile_check(profile_path=profile, settings_path=settings)

    assert result["status"] == "PROFILE_BOUND"
    assert result["settings_path"] == str(settings.resolve())
    assert result["configuration_initialized"] is False
    assert result["configuration_read"] is False
    assert result["database_access"] is False
    assert result["provider_calls"] == result["local_write_count"] == 0
    assert not (tmp_path / "data").exists()


def test_runtime_profile_check_rejects_mismatched_settings_and_cli_does_not_start(tmp_path, monkeypatch, capsys):
    runtime = _module()
    selected = tmp_path / "settings.json"; selected.write_text("{}", encoding="utf-8")
    other = tmp_path / "other.json"; other.write_text("{}", encoding="utf-8")
    profile = tmp_path / "runtime-profile.json"
    profile.write_text(json.dumps({
        "profile_id": "fixture-profile", "settings_path": str(other.resolve()),
        "stores": {name: str((tmp_path / name).resolve()) for name in ("catalog", "reports_release", "workbench", "ozon")},
    }), encoding="utf-8")
    monkeypatch.setattr(runtime, "start_runtime", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not start")))

    assert runtime.main(["--profile-check", "--profile", str(profile), "--settings-path", str(selected)]) == 1
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["status"] == "PROFILE_REJECTED"
    assert rejected["runtime_started"] is False


def test_launcher_only_passes_explicit_settings_when_profile_pair_is_bound(tmp_path, monkeypatch):
    runtime = _module()
    profile = tmp_path / "runtime-profile.json"; settings = tmp_path / "settings.json"
    profile.write_text("{}", encoding="utf-8"); settings.write_text("{}", encoding="utf-8")
    spec = runtime.service_specs(root=ROOT, executable="python", profile_path=profile, settings_path=settings)[0]
    observed = {}

    class Process:
        pid = 7654

    monkeypatch.setattr(runtime.subprocess, "Popen", lambda _command, **kwargs: observed.update(kwargs) or Process())
    runtime.launch_service(spec, runtime_dir=tmp_path / "logs")

    assert observed["env"]["ORBIT_RUNTIME_PROFILE"] == str(profile)
    assert observed["env"]["ORBIT_HIVE_SETTINGS"] == str(settings)


def test_settings_path_cannot_bypass_the_explicit_profile_contract(tmp_path, monkeypatch):
    runtime = _module()
    settings = tmp_path / "settings.json"; settings.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="profile"):
        runtime.service_specs(root=ROOT, executable="python", settings_path=settings)
    direct = runtime.ServiceSpec(
        "fixture", 8765, "http://127.0.0.1:8765/health", "fixture", ("python",), str(ROOT), None, str(settings)
    )
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not launch")))
    with pytest.raises(ValueError, match="profile"):
        runtime.launch_service(direct, runtime_dir=tmp_path / "logs")
    profile_only = runtime.service_specs(root=ROOT, executable="python", profile_path=tmp_path / "profile.json")[0]
    assert profile_only.profile_path and profile_only.settings_path is None
