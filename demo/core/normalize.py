"""
Canonicalisation helpers.

Rule: the raw value the user typed or the site published is always kept.
These functions produce the *comparable* form that matching runs on.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# Legal forms stripped from a company name before comparison. Order matters:
# longer forms first, so "private limited" is removed before "limited".
LEGAL_SUFFIXES = (
    "private limited", "pvt limited", "pvt ltd", "pvt. ltd.", "p ltd",
    "public limited", "limited liability partnership", "llp",
    "limited", "ltd", "inc", "incorporated", "corporation", "corp",
    "and company", "& company", "and co", "& co", "co",
)

# Noise words that carry no identity. "steel" and "industries" are NOT here:
# they are part of how these companies are actually distinguished.
# "and" is: Indian company names write it as "&", as "and", or omit it
# entirely — "Durgapur Iron & Steel", "Durgapur Iron and Steel" and
# "Durgapur Iron Steel" are one company, and must produce one key.
NAME_NOISE = ("the", "m/s", "ms", "and")

_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9\s]")


def clean_text(value) -> str:
    """Trim, collapse whitespace, and turn None/NaN into an empty string."""
    if value is None:
        return ""
    text = str(value).strip()
    # pandas leaves these behind when a cell is empty
    if text.lower() in ("nan", "none", "null", "not found", "n/a", "na", "-"):
        return ""
    return _WS.sub(" ", text)


def strip_title_noise(title: str) -> str:
    """
    Reduce a page <title> to the part most likely to be a company name.

    Titles are usually "Company Name | Tagline - Location". We keep the first
    segment. This is a fallback only; a name read from the site body or from
    schema.org markup is always preferred.
    """
    text = clean_text(title)
    if not text:
        return ""
    # Split on the usual title separators, keep the leading segment.
    text = re.split(r"\s*[|•·]\s*|\s+[–—]\s+|\s+-\s+", text, maxsplit=1)[0]
    return clean_text(text)[:250]


def normalize_name(name: str) -> str:
    """
    Canonical company name for matching.

    "M/s S. D. Steel Pvt. Ltd." and "SD STEEL PRIVATE LIMITED"
    both become "sd steel".
    """
    text = clean_text(name).lower()
    if not text:
        return ""

    # "M/s" is a courtesy prefix, not part of the name. Strip it before
    # punctuation is removed, or the slash turns it into the tokens "m" and
    # "s" and they get merged into a false initial.
    text = re.sub(r"^m\s*/\s*s\.?\s+", "", text)

    # Spell out the ampersand before punctuation is stripped, so the legal
    # suffixes "& co" and "and co" are both still recognised below.
    text = text.replace("&", " and ")

    text = _NON_ALNUM.sub(" ", text)
    text = _WS.sub(" ", text).strip()

    # Strip legal forms from the end, repeatedly ("... pvt ltd co").
    changed = True
    while changed:
        changed = False
        for suffix in LEGAL_SUFFIXES:
            if text.endswith(" " + suffix) or text == suffix:
                text = text[: -len(suffix)].strip()
                changed = True
    tokens = [t for t in text.split() if t and t not in NAME_NOISE]
    # Join letters that were separated as initials: "s d steel" -> "sd steel"
    merged: list[str] = []
    for token in tokens:
        if len(token) == 1 and merged and len(merged[-1]) <= 2:
            merged[-1] += token
        else:
            merged.append(token)
    return " ".join(t for t in merged if t not in NAME_NOISE)


def normalize_domain(url: str) -> str:
    """Host of a URL, lowercased, without a leading www. Empty if unusable."""
    text = clean_text(url)
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    try:
        host = urlparse(text).netloc.lower()
    except ValueError:
        return ""
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host if "." in host else ""


def normalize_url(url: str) -> str:
    """A navigable URL with a scheme, or empty."""
    text = clean_text(url)
    if not text:
        return ""
    if not text.startswith(("http://", "https://")):
        if "://" in text:          # ftp:, mailto:, javascript: ...
            return ""
        text = "https://" + text
    return text.split("#", 1)[0]


def normalize_email(email: str) -> str:
    """Lowercased, trimmed address. Strips a stray mailto: prefix."""
    text = clean_text(email).lower()
    if text.startswith("mailto:"):
        text = text[7:]
    return text.strip(" .,;:<>()[]'\"")


def normalize_phone(phone: str, default_country: str = "91") -> str:
    """
    Indian phone number in E.164 form, e.g. "+919876543210".

    Returns "" when the digits cannot be a real Indian number, so callers can
    treat "" as "no usable phone" without a second check.
    """
    text = clean_text(phone)
    if not text:
        return ""
    if text.startswith("tel:"):
        text = text[4:]

    # An extension after the number is not part of it.
    text = re.split(r"(?:ext|extn|x)\.?\s*\d+$", text, flags=re.IGNORECASE)[0]

    has_plus = text.strip().startswith("+")
    digits = re.sub(r"\D", "", text)
    if not digits:
        return ""

    # Strip international/trunk prefixes down to the 10-digit subscriber number.
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("91") and len(digits) > 10:
        digits = digits[2:]
    digits = digits.lstrip("0")

    # Indian toll-free: 1800 + 6 or 7 digits.
    if digits.startswith("1800") and len(digits) in (10, 11):
        return "+" + default_country + digits

    if len(digits) != 10:
        # Not an Indian subscriber number. Keep a plausible foreign number
        # only when it was written in explicit international form.
        if has_plus and 11 <= len(digits) <= 15:
            return "+" + digits
        return ""

    return "+" + default_country + digits


def normalize_pin(pin: str) -> str:
    """Six-digit Indian PIN code, or empty."""
    digits = re.sub(r"\D", "", clean_text(pin))
    if len(digits) == 6 and digits[0] in "12345678":
        return digits
    return ""


def normalize_identifier(value: str) -> str:
    """Uppercase, no spaces or punctuation — for CIN and GSTIN."""
    return re.sub(r"[^A-Z0-9]", "", clean_text(value).upper())
