from cryptography.fernet import Fernet

from app.security import decode_legacy_stock, stock_fingerprint


def test_plaintext_stock_does_not_need_decoding_or_a_key():
    credential = "FF-30D-LICENSE-KEY"

    assert decode_legacy_stock(credential) == credential


def test_legacy_encrypted_stock_can_still_be_decoded():
    key = Fernet.generate_key().decode("ascii")
    credential = "FF-30D-LICENSE-KEY"
    ciphertext = Fernet(key.encode("ascii")).encrypt(credential.encode("utf-8")).decode("ascii")

    legacy_stock = f"legacy:{ciphertext}"
    assert legacy_stock != credential
    assert decode_legacy_stock(legacy_stock, key) == credential
    try:
        decode_legacy_stock(legacy_stock)
    except ValueError as exc:
        assert "STOCK_ENCRYPTION_KEY" in str(exc)
    else:
        raise AssertionError("Expected missing legacy key to be rejected")
    assert stock_fingerprint(credential, key) == stock_fingerprint(credential, key)
    assert stock_fingerprint(credential, key) != stock_fingerprint("FF-7D-LICENSE-KEY", key)


def test_plaintext_stock_is_not_mistaken_for_a_legacy_ciphertext_prefix():
    credential = "gAAAA-USER-KEY"

    assert decode_legacy_stock(credential) == credential
