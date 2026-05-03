"""
Tests for signing.py encryption and secret management.

Covers: SecretManager (encrypt, decrypt, is_encrypted, key derivation),
SecureWebhookSecret, convenience functions.
"""

import pytest

pytest.importorskip("cryptography")


@pytest.fixture(autouse=True)
def _reset_secret_manager():
    """Reset global singleton between tests."""
    from soniq.features import signing

    signing._secret_manager = None
    yield
    signing._secret_manager = None


class TestSecretManager:
    def test_encrypt_and_decrypt_roundtrip(self):
        from soniq.features.signing import SecretManager

        mgr = SecretManager()
        plaintext = "my-super-secret-key"
        encrypted = mgr.encrypt(plaintext)
        assert encrypted != plaintext
        decrypted = mgr.decrypt(encrypted)
        assert decrypted == plaintext

    def test_encrypt_empty_returns_empty(self):
        from soniq.features.signing import SecretManager

        mgr = SecretManager()
        assert mgr.encrypt("") == ""

    def test_decrypt_empty_returns_empty(self):
        from soniq.features.signing import SecretManager

        mgr = SecretManager()
        assert mgr.decrypt("") == ""

    def test_decrypt_invalid_raises(self):
        from soniq.features.signing import SecretManager

        mgr = SecretManager()
        with pytest.raises((ValueError, Exception)):
            mgr.decrypt("short")

    def test_is_encrypted_detects_encrypted(self):
        from soniq.features.signing import SecretManager

        mgr = SecretManager()
        encrypted = mgr.encrypt("test-secret")
        assert mgr.is_encrypted(encrypted) is True

    def test_is_encrypted_detects_plaintext(self):
        from soniq.features.signing import SecretManager

        mgr = SecretManager()
        assert mgr.is_encrypted("just-a-password") is False

    def test_is_encrypted_empty_is_false(self):
        from soniq.features.signing import SecretManager

        mgr = SecretManager()
        assert mgr.is_encrypted("") is False

    def test_encrypt_produces_unique_ciphertexts(self):
        """Each encryption should use a random salt, producing different output."""
        from soniq.features.signing import SecretManager

        mgr = SecretManager()
        e1 = mgr.encrypt("same-secret")
        e2 = mgr.encrypt("same-secret")
        assert e1 != e2  # Different random salts

    def test_uses_env_secret_key(self, monkeypatch):
        from soniq.features.signing import SecretManager

        monkeypatch.setenv("SONIQ_SECRET_KEY", "my-fixed-key-for-testing")
        mgr = SecretManager()
        encrypted = mgr.encrypt("hello")
        decrypted = mgr.decrypt(encrypted)
        assert decrypted == "hello"


class TestConvenienceFunctions:
    def test_encrypt_secret(self):
        from soniq.features.signing import encrypt_secret

        result = encrypt_secret("test")
        assert result != "test"
        assert len(result) > 0

    def test_decrypt_secret(self):
        from soniq.features.signing import decrypt_secret, encrypt_secret

        encrypted = encrypt_secret("roundtrip")
        assert decrypt_secret(encrypted) == "roundtrip"

    def test_is_secret_encrypted(self):
        from soniq.features.signing import encrypt_secret, is_secret_encrypted

        assert is_secret_encrypted("not-encrypted") is False
        assert is_secret_encrypted(encrypt_secret("test")) is True


class TestSecureWebhookSecret:
    def test_plaintext_secret_is_encrypted(self):
        from soniq.features.signing import SecureWebhookSecret

        secret = SecureWebhookSecret("my-webhook-secret")
        assert secret.encrypted is not None
        assert secret.encrypted != "my-webhook-secret"
        assert secret.plaintext == "my-webhook-secret"

    def test_encrypted_secret_is_stored(self):
        from soniq.features.signing import SecureWebhookSecret, encrypt_secret

        pre_encrypted = encrypt_secret("test-secret")
        secret = SecureWebhookSecret(pre_encrypted)
        assert secret.encrypted == pre_encrypted
        assert secret.plaintext == "test-secret"

    def test_none_secret(self):
        from soniq.features.signing import SecureWebhookSecret

        secret = SecureWebhookSecret(None)
        assert secret.encrypted is None
        assert secret.plaintext is None
        assert bool(secret) is False

    def test_str_representation(self):
        from soniq.features.signing import SecureWebhookSecret

        secret = SecureWebhookSecret("test")
        # str should return encrypted version, not plaintext
        assert str(secret) != "test"
        assert len(str(secret)) > 0

    def test_bool_true_when_set(self):
        from soniq.features.signing import SecureWebhookSecret

        assert bool(SecureWebhookSecret("test")) is True
