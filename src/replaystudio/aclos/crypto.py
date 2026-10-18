"""AES layer for ACLOS WonderfulDb files.

ACLOS stores each account's highlight index as one file whose *text content*
is a hex string. Decoding that hex yields AES-256-CBC ciphertext:

    file bytes  --utf8-->  hex text  --hex-->  ciphertext
    ciphertext  --AES-256-CBC/Pkcs7-->  plaintext JSON

The key material is derived from the account openid (the file name):

    sha_hex = sha256(openid).hexdigest()      # 64 ASCII hex chars
    key     = sha_hex[:32] as ASCII bytes     # 32 bytes -> AES-256
    iv      = sha_hex[:16] as ASCII bytes     # 16 bytes -> CBC IV

Note the subtlety: the key is the *ASCII characters* of the hex digest, not
the 16 raw bytes the digest represents. Getting this wrong is the single most
common reason a hand-written ACLOS reader fails.

This module is a clean-room Python implementation of the scheme documented in
docs/ACLOS_FORMAT.md. It performs no writes of any kind.
"""
from __future__ import annotations

import hashlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def derive_key_iv(openid: str) -> tuple[bytes, bytes]:
    """Derive the AES-256 key and CBC IV from an account openid."""
    sha_hex = hashlib.sha256(openid.encode("utf-8")).hexdigest()
    return sha_hex[:32].encode("utf-8"), sha_hex[:16].encode("utf-8")


def decode_hex(text: str) -> bytes:
    """Strict hex decode. Rejects odd length and non-hex characters."""
    stripped = "".join(text.split())
    if len(stripped) % 2 != 0:
        raise ValueError("hex payload has odd length")
    return bytes.fromhex(stripped)


def aes_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-256-CBC decrypt with PKCS#7 padding removed manually."""
    if len(ciphertext) == 0 or len(ciphertext) % 16 != 0:
        raise ValueError(f"ciphertext length {len(ciphertext)} is not a multiple of the AES block size")
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plain = decryptor.update(ciphertext) + decryptor.finalize()
    pad = plain[-1]
    if 1 <= pad <= 16 and plain[-pad:] == bytes([pad]) * pad:
        plain = plain[:-pad]
    return plain


def decrypt_account_file(data: bytes, openid: str) -> bytes:
    """Full pipeline: raw file bytes -> plaintext JSON bytes."""
    key, iv = derive_key_iv(openid)
    return aes_decrypt(decode_hex(data.decode("utf-8")), key, iv)
