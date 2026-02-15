"""
Example: Property-based testing for email normalization.

Demonstrates:
- Idempotence property (f(f(x)) == f(x))
- Validation properties (accepts valid, rejects invalid)
- Bounded strategies for realistic inputs
"""

import re
from hypothesis import given, strategies as st, example

# ============================================================================
# Application Code
# ============================================================================


def normalize_email(email: str) -> str:
    """
    Normalize email address to canonical form.

    Rules:
    - Lowercase the entire email
    - Strip whitespace
    - Remove dots from Gmail local part (before @)

    Returns:
        Normalized email address
    """
    email = email.strip().lower()

    # Gmail-specific: remove dots from local part
    if "@gmail.com" in email:
        local, domain = email.split("@", 1)
        local = local.replace(".", "")
        email = f"{local}@{domain}"

    return email


def is_valid_email(email: str) -> bool:
    """
    Validate email format (simplified).

    Rules:
    - Contains exactly one @
    - Local part is non-empty
    - Domain part has at least one dot
    """
    if email.count("@") != 1:
        return False

    local, domain = email.split("@")

    if not local or not domain:
        return False

    if "." not in domain:
        return False

    return True


# ============================================================================
# Strategies
# ============================================================================


# Simple email strategy (for demonstration)
# In production, use hypothesis.strategies.emails()
simple_emails = st.builds(
    lambda local, domain: f"{local}@{domain}",
    local=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789.", min_size=1, max_size=20
    ),
    domain=st.sampled_from(["example.com", "gmail.com", "test.org"]),
)


# ============================================================================
# Property Tests
# ============================================================================


@given(simple_emails)
def test_normalization_is_idempotent(email):
    """
    Property: Normalizing twice gives same result as normalizing once.

    This is the definition of idempotence: f(f(x)) == f(x)
    """
    once = normalize_email(email)
    twice = normalize_email(once)

    assert once == twice


@given(simple_emails)
def test_normalized_email_is_lowercase(email):
    """
    Property: Normalized emails are always lowercase.
    """
    normalized = normalize_email(email)

    assert normalized == normalized.lower()


@given(simple_emails)
def test_normalized_email_has_no_whitespace(email):
    """
    Property: Normalized emails have no leading/trailing whitespace.
    """
    normalized = normalize_email(email)

    assert normalized == normalized.strip()


@given(simple_emails)
def test_normalization_preserves_domain(email):
    """
    Property: Domain part should be preserved (only local part changes).
    """
    if "@" in email:
        original_domain = email.split("@")[1].strip().lower()
        normalized = normalize_email(email)

        if "@" in normalized:
            normalized_domain = normalized.split("@")[1]
            assert normalized_domain == original_domain


@given(
    st.builds(
        lambda local: f"{local}@gmail.com",
        local=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=20),
    )
)
def test_gmail_normalization_removes_dots(gmail_email):
    """
    Property: Gmail normalization removes dots from local part.
    """
    # Add dots to local part
    local, domain = gmail_email.split("@")
    dotted_email = f"{local[0]}.{local[1:]}@{domain}"

    normalized_dotted = normalize_email(dotted_email)
    normalized_plain = normalize_email(gmail_email)

    # Both should normalize to same value
    assert normalized_dotted == normalized_plain


# ============================================================================
# Validation Properties
# ============================================================================


@given(simple_emails)
def test_valid_emails_pass_validation(email):
    """
    Property: Well-formed emails should pass validation.
    """
    # Our simple strategy only generates valid emails
    assert is_valid_email(email)


@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz", max_size=50))
def test_strings_without_at_sign_are_invalid(text):
    """
    Property: Strings without @ are not valid emails.
    """
    if "@" not in text:
        assert not is_valid_email(text)


@given(st.text(max_size=50))
def test_empty_local_or_domain_is_invalid(text):
    """
    Property: Emails with empty local or domain parts are invalid.
    """
    # Add cases with empty local or domain
    if text:
        assert not is_valid_email(f"@{text}")  # Empty local
        assert not is_valid_email(f"{text}@")  # Empty domain


# ============================================================================
# Regression Tests (Found by Hypothesis)
# ============================================================================


@example("Alice.Smith@Gmail.com")
@example("  alice@example.com  ")
@given(simple_emails)
def test_normalization_examples(email):
    """
    Specific examples to ensure normalization works correctly.
    """
    normalized = normalize_email(email)

    # Always lowercase
    assert normalized.islower()

    # No whitespace
    assert normalized == normalized.strip()


def test_gmail_dot_removal_specific_case():
    """
    Regression test: Gmail dots should be removed.
    """
    assert normalize_email("alice.smith@gmail.com") == "alicesmith@gmail.com"
    assert normalize_email("a.l.i.c.e@gmail.com") == "alice@gmail.com"


def test_non_gmail_dots_preserved():
    """
    Regression test: Non-Gmail emails keep dots.
    """
    assert normalize_email("alice.smith@example.com") == "alice.smith@example.com"


def test_whitespace_stripped():
    """
    Regression test: Whitespace is stripped.
    """
    assert normalize_email("  alice@example.com  ") == "alice@example.com"
    assert normalize_email("\talice@example.com\n") == "alice@example.com"
