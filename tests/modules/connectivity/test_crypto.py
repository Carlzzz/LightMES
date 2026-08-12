from lightmes.modules.connectivity.crypto import encrypt_password, decrypt_password


def test_encrypt_decrypt_round_trip():
    plaintext = "s3cr3t-pa$$w0rd"
    encrypted = encrypt_password(plaintext)
    assert encrypted != plaintext
    assert encrypted.startswith("gAAAAA")  # Fernet token prefix
    assert decrypt_password(encrypted) == plaintext


def test_encrypt_distinct_each_call():
    """Each encryption produces different ciphertext (Fernet random IV)."""
    p = "same-password"
    e1 = encrypt_password(p)
    e2 = encrypt_password(p)
    assert e1 != e2
    assert decrypt_password(e1) == decrypt_password(e2) == p


def test_decrypt_none_returns_none():
    assert decrypt_password(None) is None


def test_decrypt_empty_returns_none():
    assert decrypt_password("") is None


def test_decrypt_invalid_returns_none():
    """Wrong key / corrupted ciphertext → None (no exception)."""
    assert decrypt_password("not-a-valid-fernet-token") is None
