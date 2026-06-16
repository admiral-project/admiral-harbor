# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from app.secrets_manager import SecretsManager


def test_secrets_manager_encrypt_decrypt_round_trip():
    manager = SecretsManager("master-key")

    ciphertext = manager.encrypt("super-secret-value")

    assert ciphertext.startswith("gAAAA")
    assert manager.decrypt(ciphertext) == "super-secret-value"


def test_secrets_manager_treats_plaintext_as_legacy_value():
    manager = SecretsManager("master-key")

    assert manager.decrypt("legacy-plaintext") == "legacy-plaintext"


def test_secrets_manager_returns_unreadable_fernet_token_as_is():
    manager = SecretsManager("master-key")

    token = "gAAAAinvalid-token"

    assert manager.decrypt(token) == token
