"""OS-keyring credential storage — cross-platform alternative to JSON files.

Uses the ``keyring`` library (Windows Credential Manager / macOS Keychain /
Linux Secret Service). Mirrors the ``credentials.py`` public API so callers
can swap backends transparently:

    save_credential(provider, data)      → keyring.set_password
    load_credential(provider)            → keyring.get_password + json.loads
    list_credentials()                    → keyring service index (best-effort)

Behavior (per alpha B3 directive):
- Prefer keyring when :func:`is_available` returns True.
- Fall back to JSON storage with a one-time migration prompt when keyring is
  unavailable (headless Linux without Secret Service, CI runners, etc.).
- ``nellie auth migrate`` CLI subcommand moves existing JSON creds into
  keyring and removes the JSON files.

Security invariants:
- Credential values are serialized as JSON and stored as the keyring
  "password" field. The keyring backend determines at-rest encryption.
- Service name ``KARNA_SERVICE`` is constant so multiple Karna installs
  on the same machine share the vault.
- We keep a plaintext *index* of provider names under
  ``~/.karna/credentials/keyring.index.json`` so ``list_credentials`` is
  fast and backend-independent. The index contains NO secret data — only
  provider slugs.
"""

from __future__ import annotations

import json
import logging
import os
import warnings
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

KARNA_SERVICE = "karna-nellie"
INDEX_PATH = Path.home() / ".karna" / "credentials" / "keyring.index.json"


# ─── Availability probe ─────────────────────────────────────────────────────


def is_available() -> bool:
    """Return True if the keyring backend can actually save + load.

    The keyring library will happily import on systems with no real backend
    (Linux headless without Secret Service), then fall back to a fail-raising
    null backend. We probe with a write-read-delete round trip so the caller
    finds out cheaply.
    """
    try:
        # Late import so the module imports even when `keyring` isn't installed.
        import keyring  # type: ignore[import-untyped]
        import keyring.errors  # type: ignore[import-untyped]
    except ImportError:
        logger.debug("keyring package not installed")
        return False

    probe_key = "__karna_probe__"
    try:
        keyring.set_password(KARNA_SERVICE, probe_key, "probe-value")
        got = keyring.get_password(KARNA_SERVICE, probe_key)
        keyring.delete_password(KARNA_SERVICE, probe_key)
        return got == "probe-value"
    except Exception as e:
        # KeyringError, NoKeyringError, PasswordDeleteError, PasswordSetError
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            logger.debug("keyring probe failed: %s", e)
        return False


# ─── Index (plaintext provider slugs) ───────────────────────────────────────


def _read_index() -> list[str]:
    if not INDEX_PATH.exists():
        return []
    try:
        return list(json.loads(INDEX_PATH.read_text()) or [])
    except (json.JSONDecodeError, OSError):
        return []


def _write_index(providers: list[str]) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(INDEX_PATH.parent, 0o700)
    old_umask = os.umask(0o077)
    try:
        INDEX_PATH.write_text(json.dumps(sorted(set(providers)), indent=2))
    finally:
        os.umask(old_umask)
    try:
        os.chmod(INDEX_PATH, 0o600)
    except OSError:
        # Windows doesn't implement POSIX chmod — keyring itself owns ACLs.
        pass


def _add_to_index(provider: str) -> None:
    entries = _read_index()
    if provider not in entries:
        entries.append(provider)
        _write_index(entries)


def _remove_from_index(provider: str) -> None:
    entries = _read_index()
    if provider in entries:
        entries.remove(provider)
        _write_index(entries)


# ─── CRUD matching credentials.py ───────────────────────────────────────────


def save_credential(provider: str, data: dict[str, Any]) -> None:
    """Persist ``data`` under the keyring service for ``provider``.

    Mirrors credentials.save_credential's signature. Data is serialized as
    JSON and stored as the keyring password. Updates the plaintext index.
    """
    import keyring  # type: ignore[import-untyped]

    serialized = json.dumps(data)
    keyring.set_password(KARNA_SERVICE, provider, serialized)
    _add_to_index(provider)
    _log("saved", provider, data)


def load_credential(provider: str) -> dict[str, Any]:
    """Return the credential dict for ``provider``.

    Raises :class:`KeyError` when no credential exists — matches the
    caller-visible semantics of credentials.CredentialNotFoundError without
    cross-importing.
    """
    import keyring  # type: ignore[import-untyped]

    raw = keyring.get_password(KARNA_SERVICE, provider)
    if raw is None:
        raise KeyError(f"No credential for {provider!r} in keyring. Run: nellie auth login {provider}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        # Historical save or tampering — don't crash, return raw wrapped
        logger.warning("credential for %s is not valid JSON: %s", provider, e)
        return {"api_key": raw}


def list_credentials() -> list[str]:
    """Return the list of providers with keyring credentials.

    Reads the plaintext index. The index may drift from the actual keyring
    contents if an admin manipulated the vault outside Karna — ``verify()``
    can resync if that ever matters.
    """
    return list(_read_index())


def delete_credential(provider: str) -> bool:
    """Remove ``provider``'s credential from keyring. Returns True on success."""
    import keyring  # type: ignore[import-untyped]
    import keyring.errors  # type: ignore[import-untyped]

    try:
        keyring.delete_password(KARNA_SERVICE, provider)
        _remove_from_index(provider)
        _log("deleted", provider, {})
        return True
    except keyring.errors.PasswordDeleteError:
        # Not found in vault — still clean up the index
        _remove_from_index(provider)
        return False


def verify() -> dict[str, str]:
    """Cross-check the index against the actual vault. Returns a report.

    Keys: "indexed_only" (in index, missing from vault),
          "vault_only" (in vault, missing from index — heuristic-limited),
          "in_sync".
    """
    import keyring  # type: ignore[import-untyped]

    indexed = set(_read_index())
    missing = []
    for p in indexed:
        if keyring.get_password(KARNA_SERVICE, p) is None:
            missing.append(p)
    in_sync = sorted(indexed - set(missing))
    return {
        "indexed_only": ",".join(sorted(missing)),
        "vault_only": "",  # keyring has no list_all — best-effort empty
        "in_sync": ",".join(in_sync),
    }


# ─── Migration from JSON → keyring ──────────────────────────────────────────


def migrate_from_json(*, delete_json: bool = True) -> dict[str, Any]:
    """Move every JSON credential under ``~/.karna/credentials/`` into keyring.

    Returns a report: ``{"migrated": [...], "skipped": [...], "errors": [...]}``.

    If ``delete_json=True``, deletes each JSON file after successful
    migration. Default True — the whole point of migrating is to stop
    keeping plaintext copies on disk.
    """
    from karna.auth import credentials as c

    report: dict[str, list[str]] = {"migrated": [], "skipped": [], "errors": []}
    if not c.CREDENTIALS_DIR.exists():
        return report

    for p in c.CREDENTIALS_DIR.glob("*.token.json"):
        provider = p.stem.removesuffix(".token")
        try:
            data = json.loads(p.read_text())
            save_credential(provider, data)
            if delete_json:
                # Unlink after confirming we can load it back — belt + suspenders
                roundtrip = load_credential(provider)
                if roundtrip == data:
                    p.unlink()
                    report["migrated"].append(provider)
                else:
                    report["errors"].append(f"{provider}: roundtrip mismatch")
            else:
                report["migrated"].append(provider)
        except Exception as e:  # noqa: BLE001
            report["errors"].append(f"{provider}: {e}")
    return report


# ─── Logging ────────────────────────────────────────────────────────────────


def _log(action: str, provider: str, data: dict[str, Any]) -> None:
    """Log credential activity without ever printing secrets."""
    fingerprint = ""
    if data:
        # 8-char prefix of the api_key value, matching credentials.py behavior
        key = data.get("api_key") or next(iter(data.values()), "")
        if isinstance(key, str) and key:
            fingerprint = f" fp={key[:8]}..."
    logger.info("keyring credential %s provider=%s%s", action, provider, fingerprint)
