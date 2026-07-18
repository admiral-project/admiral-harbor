# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from app.validation import is_valid_email


def test_is_valid_email_not_string():
    assert is_valid_email(None) is False
    assert is_valid_email(123) is False
    assert is_valid_email(["test@example.com"]) is False


def test_is_valid_email_too_long():
    # Length of address > 254
    local_part = "a" * 64
    domain_part = ("b" * 60 + ".") * 3 + "com"  # 183 + 3 = 186
    # Total length: 64 + 1 + 186 = 251 (valid)
    assert is_valid_email(f"{local_part}@{domain_part}") is True

    # Make it 255 chars
    local_part_long = "a" * 64
    domain_part_long = ("b" * 62 + ".") * 3 + "com"  # 189 + 3 = 192
    email = f"{local_part_long}@{domain_part_long}"
    assert len(email) > 254
    assert is_valid_email(email) is False


def test_is_valid_email_incorrect_at_count():
    assert is_valid_email("example.com") is False
    assert is_valid_email("test@@example.com") is False
    assert is_valid_email("test@sub@example.com") is False


def test_is_valid_email_local_part_checks():
    # Empty local part
    assert is_valid_email("@example.com") is False
    # Too long local part (> 64)
    assert is_valid_email("a" * 65 + "@example.com") is False
    # Starts with a dot
    assert is_valid_email(".test@example.com") is False
    # Ends with a dot
    assert is_valid_email("test.@example.com") is False
    # Double dot
    assert is_valid_email("te..st@example.com") is False
    # Invalid characters in local part
    assert is_valid_email("te st@example.com") is False
    assert is_valid_email("test()@example.com") is False


def test_is_valid_email_domain_part_checks():
    # No dot in domain
    assert is_valid_email("test@com") is False
    # Empty label in domain (e.g. test@example..com)
    assert is_valid_email("test@example..com") is False
    # Label starts or ends with hyphen
    assert is_valid_email("test@-example.com") is False
    assert is_valid_email("test@example-.com") is False
    # Label too long (> 63 chars)
    long_label = "a" * 64
    assert is_valid_email(f"test@{long_label}.com") is False
    # Domain too long (> 253 chars)
    # A single domain part is max 253.
    # Let's construct a domain that is 254 characters:
    domain_part = ("a" * 63 + ".") * 3 + "a" * 62  # 64 * 3 + 62 = 192 + 62 = 254 characters
    assert len(domain_part) == 254
    assert is_valid_email(f"t@{domain_part}") is False


def test_is_valid_email_valid_cases():
    assert is_valid_email("test@example.com") is True
    assert is_valid_email("test.name@sub.example.co.uk") is True
    assert is_valid_email("test+alias@example.com") is True
    assert is_valid_email("123456@example.com") is True
    assert is_valid_email("  test@example.com  ") is True  # strip is called
