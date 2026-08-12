"""Password encryption for MQTT connection credentials (Fernet symmetric)."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from lightmes.config import get_settings

_SALT = b"lightmes-mqtt-encryption-salt"  # fixed salt (single-tenant internal MES)

# Lazy-init Fernet (settings loaded once at import time)
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        secret = get_settings().secret_key.encode("utf-8")
        key = hashlib.pbkdf2_hmac("sha256", secret, _SALT, iterations=100_000, dklen=32)
        _fernet = Fernet(base64.urlsafe_b64encode(key))
    return _fernet


def encrypt_password(plaintext: str) -> str:
    """Encrypt a plaintext password → Fernet token string."""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_password(ciphertext: str | None) -> str | None:
    """Decrypt a Fernet token. Returns None on None/empty/invalid input (no exception)."""
    if not ciphertext:
        return None
    try:
        return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, Exception):
        return None
