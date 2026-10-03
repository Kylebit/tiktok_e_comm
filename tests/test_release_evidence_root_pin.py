import pytest

from shared_platform import release_control


def test_release_evidence_root_pin_is_absolute_existing_and_explicit(tmp_path, monkeypatch):
    authority = tmp_path / "authority"
    authority.mkdir()
    monkeypatch.setenv(release_control.RELEASE_EVIDENCE_ROOT_ENV, str(authority.resolve()))

    assert release_control.configured_release_evidence_root(tmp_path / "candidate") == authority.resolve()


def test_release_evidence_root_pin_rejects_relative_path(tmp_path, monkeypatch):
    monkeypatch.setenv(release_control.RELEASE_EVIDENCE_ROOT_ENV, "relative/evidence")

    with pytest.raises(ValueError, match="must be an absolute path"):
        release_control.configured_release_evidence_root(tmp_path)


def test_release_source_identity_pin_is_absolute_existing_file(tmp_path, monkeypatch):
    evidence = tmp_path / "source.json"
    evidence.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(
        release_control.RELEASE_SOURCE_IDENTITY_PATH_ENV,
        str(evidence.resolve()),
    )
    monkeypatch.setenv(release_control.RELEASE_SOURCE_IDENTITY_OFFER_ENV, "395")
    monkeypatch.setenv(
        release_control.RELEASE_SOURCE_IDENTITY_SHA256_ENV,
        __import__("hashlib").sha256(evidence.read_bytes()).hexdigest(),
    )

    assert release_control.configured_release_source_identity_path("395") == evidence.resolve()
    assert release_control.configured_release_source_identity_path("396") is None


def test_release_source_identity_pin_rejects_relative_path(monkeypatch):
    monkeypatch.setenv(
        release_control.RELEASE_SOURCE_IDENTITY_PATH_ENV,
        "relative/source.json",
    )
    monkeypatch.setenv(release_control.RELEASE_SOURCE_IDENTITY_OFFER_ENV, "395")
    monkeypatch.setenv(release_control.RELEASE_SOURCE_IDENTITY_SHA256_ENV, "0" * 64)

    with pytest.raises(ValueError, match="must be an absolute path"):
        release_control.configured_release_source_identity_path("395")


def test_release_source_identity_pin_revalidates_digest(tmp_path, monkeypatch):
    evidence = tmp_path / "source.json"
    evidence.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(release_control.RELEASE_SOURCE_IDENTITY_PATH_ENV, str(evidence))
    monkeypatch.setenv(release_control.RELEASE_SOURCE_IDENTITY_OFFER_ENV, "395")
    monkeypatch.setenv(release_control.RELEASE_SOURCE_IDENTITY_SHA256_ENV, "0" * 64)

    with pytest.raises(ValueError, match="digest drifted"):
        release_control.configured_release_source_identity_path("395")
