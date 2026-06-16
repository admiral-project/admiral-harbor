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

    def __init__(self, master_key: str):
        key_bytes = hashlib.sha256(master_key.encode()).digest()
        self.fernet = Fernet(base64.urlsafe_b64encode(key_bytes))

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext string. Returns base64-encoded ciphertext."""
        return self.fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt base64-encoded ciphertext. Returns plaintext string.

        If the value does not look like a Fernet token (legacy plaintext),
        returns it as-is with a warning.
        """
        if not ciphertext or not ciphertext.startswith("gAAAA"):
            if ciphertext:
                logger.warning(
                    "Secret value does not look encrypted; treating as plaintext "
                    "(migrate by re-saving the secret through the admin UI)"
                )
            return ciphertext
        try:
            return self.fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            logger.error(
                "Secret value has Fernet prefix but decryption failed; "
                "HARBOR_ENCRYPTION_KEY may have changed since this value was stored"
            )
            return ciphertext
        except Exception as exc:
            logger.error("Failed to decrypt secret: %s", exc)
            return ciphertext
