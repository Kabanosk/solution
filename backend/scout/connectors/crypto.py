"""Fernet sealing of connection secrets (OAuth refresh tokens). The key comes from `CONNECTIONS_KEY`."""

from cryptography.fernet import Fernet


def seal(text: str, key: str) -> bytes:
    return Fernet(key.encode()).encrypt(text.encode())


def open_(data: bytes, key: str) -> str:
    return Fernet(key.encode()).decrypt(bytes(data)).decode()


def new_key() -> str:
    return Fernet.generate_key().decode()
