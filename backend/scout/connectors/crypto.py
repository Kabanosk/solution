"""Fernet sealing of connection secrets (OAuth refresh tokens). The key comes from `CONNECTIONS_KEY`."""

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet


def seal(text: str, key: str) -> bytes:
    return Fernet(key.encode()).encrypt(text.encode())


def open_(data: bytes, key: str) -> str:
    return Fernet(key.encode()).decrypt(bytes(data)).decode()


def new_key() -> str:
    return Fernet.generate_key().decode()


def derive_key(master: str, organization_id: str, connection_id: str) -> str:
    """A Fernet key bound to one organization and one connection. A ciphertext copied into another organization's
    schema (or another connection row) cannot be opened, even though the server holds a single master secret."""
    if len(master) < 32:
        raise ValueError("CONNECTIONS_KEY must be at least 32 characters")
    digest = hmac.new(master.encode(), f"scout-connection/v1|{organization_id}|{connection_id}".encode(),
                      hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode()
