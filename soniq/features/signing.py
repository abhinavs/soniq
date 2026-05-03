"""
Soniq Security Module.
Provides encryption utilities for sensitive data like webhook secrets.
"""

import base64
import os
import secrets
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from soniq.app import Soniq

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except ImportError:
    Fernet = None  # type: ignore[assignment,misc]
    hashes = None  # type: ignore[assignment]
    PBKDF2HMAC = None  # type: ignore[assignment,misc]


def _require_cryptography():
    if Fernet is None:
        raise ImportError(
            "cryptography is required for signing/encryption. "
            "Install it with: pip install soniq[webhooks]"
        )


_SALT_LENGTH = 16
_PBKDF2_ITERATIONS = 310000


class SecretManager:
    """
    Manages encryption and decryption of sensitive data like webhook secrets.

    Uses Fernet (symmetric encryption) with a per-encryption random salt.
    """

    def __init__(self):
        _require_cryptography()
        self._secret_key: str = ""
        self._initialize_encryption()

    def _initialize_encryption(self):
        """Initialize encryption with environment key or generated key"""
        secret_key = os.environ.get("SONIQ_SECRET_KEY")

        if not secret_key:
            secret_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(
                "utf-8"
            )
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(
                "No SONIQ_SECRET_KEY environment variable found. "
                "Using auto-generated key. For production, set SONIQ_SECRET_KEY."
            )

        self._secret_key = secret_key

    @staticmethod
    def _derive_key(
        password: str, salt: bytes, iterations: int = _PBKDF2_ITERATIONS
    ) -> bytes:
        """Derive encryption key from password using PBKDF2"""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=iterations,
        )
        return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a plaintext string using a random salt.

        The output format is base64(salt + fernet_token), which embeds
        the salt so each encryption uses unique key material.
        """
        if not plaintext:
            return plaintext

        salt = os.urandom(_SALT_LENGTH)
        key = self._derive_key(self._secret_key, salt)
        fernet = Fernet(key)
        token = fernet.encrypt(plaintext.encode("utf-8"))
        return base64.urlsafe_b64encode(salt + token).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt a ciphertext string.

        Format: base64(salt + fernet_token) where salt is the first 16 bytes.
        """
        if not ciphertext:
            return ciphertext

        raw = base64.urlsafe_b64decode(ciphertext.encode("utf-8"))

        if len(raw) <= _SALT_LENGTH:
            raise ValueError("Invalid ciphertext: too short")

        salt = raw[:_SALT_LENGTH]
        token = raw[_SALT_LENGTH:]

        try:
            key = self._derive_key(self._secret_key, salt)
            fernet = Fernet(key)
            result: str = fernet.decrypt(token).decode("utf-8")
            return result
        except Exception as e:
            raise ValueError(f"Failed to decrypt secret: {e}") from e

    def is_encrypted(self, value: str) -> bool:
        """
        Check if a string appears to be encrypted (basic heuristic).

        This is not foolproof but helps with migration from plaintext.
        """
        if not value:
            return False

        try:
            decoded = base64.urlsafe_b64decode(value.encode("utf-8"))
            # 16-byte salt + Fernet token (min 73 bytes)
            return len(decoded) >= _SALT_LENGTH + 73
        except Exception:
            return False


class SigningService:
    """Signing/encryption service bound to a Soniq instance.

    Signing has no database state today, so the service is a thin shell;
    it exists for symmetry with the other feature services so callers can
    construct ``SigningService(app)`` and reach encryption helpers through
    a uniform surface. Future additions (key rotation, per-tenant keys)
    will hang off the bound app's settings.
    """

    def __init__(self, app: "Soniq"):
        self._app = app
        self.manager = get_secret_manager()

    def encrypt(self, plaintext: str) -> str:
        return self.manager.encrypt(plaintext)

    def decrypt(self, ciphertext: str) -> str:
        return self.manager.decrypt(ciphertext)

    def is_encrypted(self, value: str) -> bool:
        return self.manager.is_encrypted(value)


# Global secret manager instance
_secret_manager: Optional[SecretManager] = None


def get_secret_manager() -> SecretManager:
    """Get or create the global secret manager instance"""
    global _secret_manager
    if _secret_manager is None:
        _secret_manager = SecretManager()
    return _secret_manager


def encrypt_secret(plaintext: str) -> str:
    """Convenience function to encrypt a secret"""
    return get_secret_manager().encrypt(plaintext)


def decrypt_secret(ciphertext: str) -> str:
    """Convenience function to decrypt a secret"""
    return get_secret_manager().decrypt(ciphertext)


def is_secret_encrypted(value: str) -> bool:
    """Convenience function to check if a value is encrypted"""
    return get_secret_manager().is_encrypted(value)


class SecureWebhookSecret:
    """
    Secure wrapper for webhook secrets that automatically handles encryption/decryption.
    """

    def __init__(self, secret: Union[str, None]):
        self._encrypted_secret: Optional[str] = None
        self._plaintext_secret: Optional[str] = None

        if secret:
            if is_secret_encrypted(secret):
                # Already encrypted
                self._encrypted_secret = secret
            else:
                # Plaintext - encrypt it
                self._plaintext_secret = secret
                self._encrypted_secret = encrypt_secret(secret)

    @property
    def encrypted(self) -> Optional[str]:
        """Get the encrypted version (safe to store in database)"""
        return self._encrypted_secret

    @property
    def plaintext(self) -> Optional[str]:
        """Get the plaintext version (use for signing/verification)"""
        if self._plaintext_secret is None and self._encrypted_secret:
            self._plaintext_secret = decrypt_secret(self._encrypted_secret)
        return self._plaintext_secret

    def __str__(self) -> str:
        """String representation (encrypted for safety)"""
        return self._encrypted_secret or ""

    def __bool__(self) -> bool:
        """Boolean check"""
        return bool(self._encrypted_secret)
