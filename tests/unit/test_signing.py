"""
Tests for the signing feature module covering import checks, encryption
round-trips, random salt behaviour, and PBKDF2 iteration requirements.
"""

import pytest

pytest.importorskip("cryptography")


@pytest.fixture(autouse=True)
def set_secret_key(monkeypatch):
    """Set a deterministic secret key for all signing tests."""
    monkeypatch.setenv("SONIQ_SECRET_KEY", "test-secret-key-for-unit-tests")
    # Reset the global manager so it picks up the new key
    import soniq.features.signing as signing_mod

    signing_mod._secret_manager = None
    yield
    signing_mod._secret_manager = None


# ---------------------------------------------------------------------------
# Basic import and feature-flag tests
# ---------------------------------------------------------------------------


def test_signing_module_importable():
    """The signing module should be importable regardless of feature flags."""
    from soniq.features import signing

    assert signing is not None


def test_secret_manager_requires_cryptography():
    """SecretManager should require the cryptography package."""
    from soniq.features.signing import SecretManager, _require_cryptography

    # cryptography is installed in dev, so this should work
    _require_cryptography()
    manager = SecretManager()
    assert manager is not None


def test_signing_service_property():
    from soniq import Soniq

    app = Soniq(backend="memory")
    service = app.signing
    assert service is not None
    # Lazy: same object on the next access.
    assert app.signing is service


# ---------------------------------------------------------------------------
# Encrypt / decrypt round-trips
# ---------------------------------------------------------------------------


class TestEncryptDecryptRoundtrip:
    """Verify that encrypt followed by decrypt returns the original plaintext."""

    def test_roundtrip(self):
        from soniq.features.signing import SecretManager

        manager = SecretManager()
        plaintext = "my-webhook-secret-token"
        ciphertext = manager.encrypt(plaintext)
        assert manager.decrypt(ciphertext) == plaintext

    def test_roundtrip_unicode(self):
        from soniq.features.signing import SecretManager

        manager = SecretManager()
        plaintext = "secret-with-special-chars-!@#$%"
        assert manager.decrypt(manager.encrypt(plaintext)) == plaintext


# ---------------------------------------------------------------------------
# Legacy compatibility
# ---------------------------------------------------------------------------


class TestRandomSaltProducesDifferentCiphertexts:
    """Verify that two encryptions of the same plaintext produce different ciphertexts."""

    def test_different_ciphertexts(self):
        from soniq.features.signing import SecretManager

        manager = SecretManager()
        plaintext = "same-secret-every-time"

        ct1 = manager.encrypt(plaintext)
        ct2 = manager.encrypt(plaintext)

        assert (
            ct1 != ct2
        ), "Two encryptions of the same plaintext should differ (random salt)"
        # Both should still decrypt to the same value
        assert manager.decrypt(ct1) == plaintext
        assert manager.decrypt(ct2) == plaintext


# ---------------------------------------------------------------------------
# PBKDF2 iteration requirements (NIST 2023)
# ---------------------------------------------------------------------------


class TestPBKDF2Iterations:
    def test_iterations_at_least_310k(self):
        from soniq.features.signing import _PBKDF2_ITERATIONS

        assert (
            _PBKDF2_ITERATIONS >= 310000
        ), f"PBKDF2 iterations ({_PBKDF2_ITERATIONS}) below NIST 2023 recommendation (310,000)"

    def test_encrypt_decrypt_roundtrip_with_new_iterations(self):
        from soniq.features.signing import SecretManager

        mgr = SecretManager()
        plaintext = "sensitive-webhook-secret-value"
        encrypted = mgr.encrypt(plaintext)
        decrypted = mgr.decrypt(encrypted)
        assert decrypted == plaintext
