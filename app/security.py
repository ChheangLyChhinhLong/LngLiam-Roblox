import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken


def decode_legacy_stock(value: str, key: str = "") -> str:
    legacy_prefix = "legacy:"
    if not value.startswith(legacy_prefix):
        return value
    if not key:
        raise ValueError("STOCK_ENCRYPTION_KEY is required for legacy encrypted stock")
    try:
        token = value.removeprefix(legacy_prefix)
        return Fernet(key.encode("ascii")).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise ValueError("Cannot decrypt legacy stock; verify STOCK_ENCRYPTION_KEY") from exc


def stock_fingerprint(value: str, key: str) -> str:
    secret = base64.urlsafe_b64decode(key.encode("ascii"))
    return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()
