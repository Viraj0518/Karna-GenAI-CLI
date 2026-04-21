"""Tests for karna/auth/keyring_store.py — mocked keyring backend.

No live Secret Service / Keychain access — every test mocks the `keyring`
module's set/get/delete functions. Verifies the save/load/list/delete/
migrate contract matches credentials.py so callers can swap backends.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

# ─── Mock keyring fixture ───────────────────────────────────────────────────


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], str]:
    """In-memory keyring backend. Returns the underlying store for assertions."""
    store: dict[tuple[str, str], str] = {}

    class _PasswordDeleteError(Exception):
        pass

    errors_mod = types.SimpleNamespace(PasswordDeleteError=_PasswordDeleteError)

    def set_password(service: str, name: str, value: str) -> None:
        store[(service, name)] = value

    def get_password(service: str, name: str) -> str | None:
        return store.get((service, name))

    def delete_password(service: str, name: str) -> None:
        if (service, name) not in store:
            raise _PasswordDeleteError(f"missing {service}/{name}")
        del store[(service, name)]

    fake = types.SimpleNamespace(
        set_password=set_password,
        get_password=get_password,
        delete_password=delete_password,
        errors=errors_mod,
    )
    monkeypatch.setitem(sys.modules, "keyring", fake)
    monkeypatch.setitem(sys.modules, "keyring.errors", errors_mod)
    return store


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Route Path.home() through tmp_path + reset INDEX_PATH pointer."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # Reload pointer because keyring_store captured INDEX_PATH at import time
    from karna.auth import keyring_store as ks

    monkeypatch.setattr(ks, "INDEX_PATH", tmp_path / ".karna" / "credentials" / "keyring.index.json")
    # credentials module also uses Path.home() - re-point CREDENTIALS_DIR
    from karna.auth import credentials as cred

    monkeypatch.setattr(cred, "CREDENTIALS_DIR", tmp_path / ".karna" / "credentials")
    return tmp_path


# ─── CRUD ───────────────────────────────────────────────────────────────────


class TestCrud:
    def test_save_and_load(self, fake_keyring, isolated_home):
        from karna.auth import keyring_store as ks

        ks.save_credential("openrouter", {"api_key": "sk-test-abc"})
        data = ks.load_credential("openrouter")
        assert data == {"api_key": "sk-test-abc"}
        assert (ks.KARNA_SERVICE, "openrouter") in fake_keyring

    def test_load_missing_raises_keyerror(self, fake_keyring, isolated_home):
        from karna.auth import keyring_store as ks

        with pytest.raises(KeyError, match="No credential for 'openrouter'"):
            ks.load_credential("openrouter")

    def test_list_empty(self, fake_keyring, isolated_home):
        from karna.auth import keyring_store as ks

        assert ks.list_credentials() == []

    def test_list_after_save(self, fake_keyring, isolated_home):
        from karna.auth import keyring_store as ks

        ks.save_credential("openrouter", {"api_key": "sk-1"})
        ks.save_credential("anthropic", {"api_key": "sk-2"})
        assert sorted(ks.list_credentials()) == ["anthropic", "openrouter"]

    def test_delete(self, fake_keyring, isolated_home):
        from karna.auth import keyring_store as ks

        ks.save_credential("openrouter", {"api_key": "sk-1"})
        assert ks.delete_credential("openrouter") is True
        assert ks.list_credentials() == []
        # Deleting a missing provider returns False, not raising
        assert ks.delete_credential("openrouter") is False

    def test_load_non_json_fallback(self, fake_keyring, isolated_home):
        """Legacy / tampered entries should round-trip as {api_key: raw}."""
        from karna.auth import keyring_store as ks

        fake_keyring[(ks.KARNA_SERVICE, "legacy")] = "raw-string-no-json"
        assert ks.load_credential("legacy") == {"api_key": "raw-string-no-json"}


# ─── Availability probe ────────────────────────────────────────────────────


class TestAvailability:
    def test_available_when_probe_roundtrips(self, fake_keyring, isolated_home):
        from karna.auth import keyring_store as ks

        assert ks.is_available() is True

    def test_unavailable_when_set_fails(self, monkeypatch):
        """Probe catches any keyring error and returns False."""

        # Install a failing fake
        def boom(*a, **kw):
            raise RuntimeError("no backend")

        errors_mod = types.SimpleNamespace(PasswordDeleteError=RuntimeError)
        fake = types.SimpleNamespace(
            set_password=boom,
            get_password=boom,
            delete_password=boom,
            errors=errors_mod,
        )
        monkeypatch.setitem(sys.modules, "keyring", fake)
        monkeypatch.setitem(sys.modules, "keyring.errors", errors_mod)
        from karna.auth import keyring_store as ks

        assert ks.is_available() is False

    def test_unavailable_when_package_missing(self, monkeypatch):
        """No keyring installed → is_available returns False, no exception."""
        # Simulate a broken keyring package where `keyring.errors` can't be imported.
        bogus = types.ModuleType("keyring")
        # Don't attach a .set_password — the probe will fail at errors-import
        monkeypatch.setitem(sys.modules, "keyring", bogus)
        monkeypatch.delitem(sys.modules, "keyring.errors", raising=False)
        from karna.auth import keyring_store as ks

        result = ks.is_available()
        assert result is False


# ─── Migration ──────────────────────────────────────────────────────────────


class TestMigration:
    def test_migrate_moves_json_into_keyring(self, fake_keyring, isolated_home):
        from karna.auth import credentials as cred
        from karna.auth import keyring_store as ks

        cred.save_credential("openrouter", {"api_key": "sk-1"})
        cred.save_credential("anthropic", {"api_key": "sk-2"})
        report = ks.migrate_from_json()
        assert sorted(report["migrated"]) == ["anthropic", "openrouter"]
        assert sorted(ks.list_credentials()) == ["anthropic", "openrouter"]
        # JSON files must be gone
        assert cred.list_credentials() == []

    def test_migrate_with_keep_json(self, fake_keyring, isolated_home):
        from karna.auth import credentials as cred
        from karna.auth import keyring_store as ks

        cred.save_credential("openrouter", {"api_key": "sk-1"})
        report = ks.migrate_from_json(delete_json=False)
        assert report["migrated"] == ["openrouter"]
        # JSON still present
        assert cred.list_credentials() == ["openrouter"]

    def test_migrate_empty(self, fake_keyring, isolated_home):
        from karna.auth import keyring_store as ks

        report = ks.migrate_from_json()
        assert report == {"migrated": [], "skipped": [], "errors": []}


# ─── Verify / drift detection ───────────────────────────────────────────────


class TestVerify:
    def test_verify_in_sync(self, fake_keyring, isolated_home):
        from karna.auth import keyring_store as ks

        ks.save_credential("openrouter", {"api_key": "sk-1"})
        report = ks.verify()
        assert report["in_sync"] == "openrouter"
        assert report["indexed_only"] == ""

    def test_verify_drift_index_without_vault(self, fake_keyring, isolated_home):
        from karna.auth import keyring_store as ks

        ks.save_credential("openrouter", {"api_key": "sk-1"})
        # Tamper — remove from vault directly
        del fake_keyring[(ks.KARNA_SERVICE, "openrouter")]
        report = ks.verify()
        assert report["indexed_only"] == "openrouter"
        assert report["in_sync"] == ""
