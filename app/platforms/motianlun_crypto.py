from __future__ import annotations

import base64
import hashlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


def _aes_key(service_key: str) -> bytes:
    return hashlib.sha1(service_key.encode("utf-8")).digest()[:16]


def encrypt_profile_value(value: str, service_key: str) -> str:
    padder = PKCS7(128).padder()
    padded = padder.update(value.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_aes_key(service_key)), modes.ECB()).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode("ascii")


def decrypt_profile_value(value: str, service_key: str) -> str:
    decryptor = Cipher(algorithms.AES(_aes_key(service_key)), modes.ECB()).decryptor()
    padded = decryptor.update(base64.b64decode(value)) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    plaintext = unpadder.update(padded) + unpadder.finalize()
    return plaintext.decode("utf-8")
