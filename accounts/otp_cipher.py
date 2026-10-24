import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _cipher():
    key = hashlib.sha256(f"{settings.SECRET_KEY}:bk-otp-display:v1".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_otp(code):
    return _cipher().encrypt(str(code).encode()).decode()


def decrypt_otp(ciphertext):
    if not ciphertext:
        return None
    try:
        return _cipher().decrypt(str(ciphertext).encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        return None
