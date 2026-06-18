# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import pytest

from app.secrets_manager import SecretsManager


def test_secrets_manager_encrypt_decrypt_round_trip():
    manager = SecretsManager("master-key")

    ciphertext = manager.encrypt("super-secret-value")

    assert ciphertext.startswith("gAAAA")
    assert manager.decrypt(ciphertext) == "super-secret-value"


def test_secrets_manager_rejects_plaintext():
    manager = SecretsManager("master-key")

    with pytest.raises(SecretsManager.EncryptionError):
        manager.decrypt("legacy-plaintext")


def test_secrets_manager_rejects_invalid_fernet_token():
    manager = SecretsManager("master-key")

    with pytest.raises(SecretsManager.EncryptionError):
        manager.decrypt("gAAAAinvalid-token")


def test_secrets_manager_rejects_empty_key():
    with pytest.raises(ValueError, match="HARBOR_ENCRYPTION_KEY"):
        SecretsManager("")


def test_secrets_manager_returns_empty_string_for_none_value():
    manager = SecretsManager("master-key")

    assert manager.decrypt("") == ""
