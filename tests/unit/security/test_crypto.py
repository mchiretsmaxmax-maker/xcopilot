"""Шифрование секретов."""

import pytest
from cryptography.fernet import Fernet

from xcopilot.security.crypto import DecryptError, SecretBox


def _key() -> str:
    return Fernet.generate_key().decode()


def test_secret_roundtrip_and_ciphertext_differs_from_plain() -> None:
    box = SecretBox({1: _key()})
    blob = box.encrypt("x-token-123")
    assert isinstance(blob, bytes)
    assert b"x-token-123" not in blob
    assert box.decrypt(blob) == "x-token-123"
    assert box.encrypt("x-token-123") != blob  # случайный IV
    assert box.key_version == 1


def test_secret_decrypt_with_wrong_key_fails() -> None:
    blob = SecretBox({1: _key()}).encrypt("secret")
    with pytest.raises(DecryptError) as exc:
        SecretBox({1: _key()}).decrypt(blob)
    assert "secret" not in str(exc.value)
    with pytest.raises(DecryptError):
        SecretBox({1: _key()}).decrypt(b"garbage")


def test_secret_rotation_reads_old_and_writes_new_version() -> None:
    k1, k2 = _key(), _key()
    old_blob = SecretBox({1: k1}).encrypt("t")
    box = SecretBox({1: k1, 2: k2})
    assert box.key_version == 2
    assert box.decrypt(old_blob) == "t"
    assert SecretBox({2: k2}).decrypt(box.encrypt("t")) == "t"


def test_secret_box_from_string() -> None:
    k1, k2 = _key(), _key()
    assert SecretBox.from_string(k1).key_version == 1
    box = SecretBox.from_string(f"1:{k1},2:{k2}")
    assert box.key_version == 2
    with pytest.raises(ValueError):
        SecretBox.from_string("")
    with pytest.raises(ValueError):
        SecretBox({})
