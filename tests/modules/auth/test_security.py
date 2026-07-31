from lightmes.shared.security import hash_password, verify_password


def test_hash_is_not_plaintext():
    h = hash_password("secret123")
    assert h != "secret123"
    assert len(h) > 20


def test_verify_correct_password():
    h = hash_password("secret123")
    assert verify_password("secret123", h) is True


def test_verify_wrong_password():
    h = hash_password("secret123")
    assert verify_password("wrong", h) is False
