import hashlib
import hmac
import re


def verify_signature(
    body: bytes,
    signature_header: str | None,
    secret: bytes,
) -> bool:
    if signature_header is None:
        return False
    if not secret:
        return False
    if re.fullmatch(r"sha256=[0-9a-f]{64}", signature_header) is None:
        return False

    expected_hash = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header, expected_hash)
