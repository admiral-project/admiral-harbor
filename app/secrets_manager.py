# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("admiral-harbor")


class SecretsManager:
    """AES-256-GCM encryption/decryption using HARBOR_ENCRYPTION_KEY.

    Mirrors admirald's internal/secrets/secrets.go pattern:
    - SHA-256 of master key -> 32-byte AES key
    - AES-256-GCM with random nonce per encryption
    - Base64-encoded output
    """

    class EncryptionError(Exception):
        """Raised when a secret cannot be decrypted."""

    def __init__(self, master_key: str):
        if not master_key:
            raise ValueError("HARBOR_ENCRYPTION_KEY is required")
        key_bytes = hashlib.sha256(master_key.encode()).digest()
        self.fernet = Fernet(base64.urlsafe_b64encode(key_bytes))

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext string. Returns base64-encoded ciphertext."""
        return self.fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt base64-encoded ciphertext. Returns plaintext string.

        Raises EncryptionError if the value is not a valid Fernet token
        or decryption fails.
        """
        if not ciphertext:
            return ""
        if not ciphertext.startswith("gAAAA"):
            raise self.EncryptionError(
                "Secret value does not look encrypted; "
                "HARBOR_ENCRYPTION_KEY may have changed or the value was "
                "stored in plaintext before encryption was enabled"
            )
        try:
            return self.fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            raise self.EncryptionError(
                "Secret value has Fernet prefix but decryption failed; "
                "HARBOR_ENCRYPTION_KEY may have changed since this value was stored"
            ) from None
        except Exception as exc:
            raise self.EncryptionError(f"Failed to decrypt secret: {exc}") from exc
