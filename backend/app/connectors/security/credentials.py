"""Authenticated encryption of connector credentials (AES-256-GCM).

Connector secrets (API keys, bearer tokens, secret header values) are stored
only as ciphertext produced here. The key comes from the dedicated
``CONNECTOR_ENCRYPTION_KEY`` environment secret, never from ``SECRET_KEY`` (the
JWT signing key), and is never written to the database.

Token format (one ASCII string per encrypted value)::

    v1:<key id>:<nonce, base64url>:<ciphertext + 16-byte tag, base64url>

* A fresh random 96-bit nonce is used for every encryption.
* The associated data binds each ciphertext to its connector and purpose
  (``opspilot:connector:<id>:<purpose>``), so a ciphertext copied into
  another connector's row, or into another field, fails to decrypt.
* The key id is a fingerprint of the key (not the key), so decryption picks the
  right key during a rotation: the primary key encrypts; the primary and every
  key in ``CONNECTOR_ENCRYPTION_PREVIOUS_KEYS`` decrypt. ``python -m app.cli
  rotate-connector-keys`` re-encrypts everything with the primary key.

Nothing in this module logs, returns or puts a key or a plaintext into an
exception message.
"""

import base64
import binascii
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import status

from app.core.exceptions import AppError

KEY_BYTES = 32
NONCE_BYTES = 12
TOKEN_VERSION = "v1"
_KEY_ID_CONTEXT = b"opspilot-connector-credential-key-id:"


class EncryptionNotConfiguredError(AppError):
    """No encryption key: credentials can be neither stored nor used. Never falls back to plaintext."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "CONNECTOR_ENCRYPTION_NOT_CONFIGURED"
    message = (
        "Connector credential encryption is not configured. Set CONNECTOR_ENCRYPTION_KEY on the server "
        "(see docs/CONNECTORS.md)."
    )


class CredentialDecryptionError(AppError):
    """A stored secret cannot be decrypted: its key is not configured, or the value was altered."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "CONNECTOR_CREDENTIALS_UNAVAILABLE"
    message = (
        "The connector's stored credentials cannot be decrypted with the configured keys. "
        "Check CONNECTOR_ENCRYPTION_KEY and CONNECTOR_ENCRYPTION_PREVIOUS_KEYS, or enter the credentials again."
    )


def _b64decode(value: str) -> bytes:
    text = value.strip()
    padded = text + "=" * (-len(text) % 4)
    try:
        if "-" in text or "_" in text:
            return base64.urlsafe_b64decode(padded)
        return base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("not valid base64") from None


def parse_key(value: str) -> bytes:
    """Decode one base64 key. Errors never include the value."""
    try:
        key = _b64decode(value)
    except ValueError:
        raise ValueError(
            "Connector encryption keys must be base64-encoded 32-byte values. Generate one with: "
            'python -c "import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"'
        ) from None
    if len(key) != KEY_BYTES:
        raise ValueError(f"Connector encryption keys must decode to exactly {KEY_BYTES} bytes (AES-256).")
    return key


def key_id(key: bytes) -> str:
    return hashlib.sha256(_KEY_ID_CONTEXT + key).hexdigest()[:16]


def _aad(connector_id: uuid.UUID, purpose: str) -> bytes:
    return f"opspilot:connector:{connector_id}:{purpose}".encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


@dataclass(frozen=True, slots=True)
class _Key:
    id: str
    aead: AESGCM


class CredentialCipher:
    """Encrypts with the primary key; decrypts with the primary or any previous key."""

    def __init__(self, primary: bytes, previous: list[bytes] | None = None) -> None:
        self._primary = _Key(key_id(primary), AESGCM(primary))
        keys = {self._primary.id: self._primary}
        for key in previous or []:
            identifier = key_id(key)
            keys.setdefault(identifier, _Key(identifier, AESGCM(key)))
        self._keys = keys

    @classmethod
    def from_settings(cls, settings: Any) -> "CredentialCipher | None":
        primary = settings.connector_encryption_key
        if primary is None:
            return None
        previous_raw = settings.connector_encryption_previous_keys
        previous = (
            [parse_key(part) for part in previous_raw.get_secret_value().split(",") if part.strip()]
            if previous_raw is not None
            else []
        )
        return cls(parse_key(primary.get_secret_value()), previous)

    @property
    def primary_key_id(self) -> str:
        return self._primary.id

    def __repr__(self) -> str:  # never show key material
        return f"CredentialCipher(primary_key_id={self._primary.id!r}, keys={len(self._keys)})"

    # --- bytes ------------------------------------------------------------------------------------------

    def encrypt(self, plaintext: str, *, connector_id: uuid.UUID, purpose: str) -> str:
        nonce = os.urandom(NONCE_BYTES)
        ciphertext = self._primary.aead.encrypt(nonce, plaintext.encode("utf-8"), _aad(connector_id, purpose))
        return f"{TOKEN_VERSION}:{self._primary.id}:{_b64(nonce)}:{_b64(ciphertext)}"

    def decrypt(self, token: str, *, connector_id: uuid.UUID, purpose: str) -> str:
        try:
            version, identifier, nonce_text, ciphertext_text = token.split(":")
        except (ValueError, AttributeError):
            raise CredentialDecryptionError() from None
        key = self._keys.get(identifier)
        if version != TOKEN_VERSION or key is None:
            raise CredentialDecryptionError()
        try:
            nonce = _b64decode(nonce_text)
            ciphertext = _b64decode(ciphertext_text)
            plaintext = key.aead.decrypt(nonce, ciphertext, _aad(connector_id, purpose))
        except (ValueError, InvalidTag):
            raise CredentialDecryptionError() from None
        return plaintext.decode("utf-8")

    # --- JSON documents -----------------------------------------------------------------------------------------

    def encrypt_json(self, value: dict[str, Any], *, connector_id: uuid.UUID, purpose: str) -> str:
        return self.encrypt(json.dumps(value, separators=(",", ":"), sort_keys=True), connector_id=connector_id, purpose=purpose)

    def decrypt_json(self, token: str, *, connector_id: uuid.UUID, purpose: str) -> dict[str, Any]:
        text = self.decrypt(token, connector_id=connector_id, purpose=purpose)
        try:
            value = json.loads(text)
        except ValueError:
            raise CredentialDecryptionError() from None
        if not isinstance(value, dict):
            raise CredentialDecryptionError()
        return value

    def needs_rotation(self, token: str) -> bool:
        """True when the value was encrypted with a key other than the primary one."""
        parts = token.split(":")
        return len(parts) != 4 or parts[1] != self._primary.id


def require_cipher(cipher: CredentialCipher | None) -> CredentialCipher:
    if cipher is None:
        raise EncryptionNotConfiguredError()
    return cipher
