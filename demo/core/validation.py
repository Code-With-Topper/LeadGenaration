"""
Validation, in tiers.

    Tier 1  syntax        free, offline
    Tier 2  plausibility  free, offline  — rejects placeholders and junk
    Tier 3  deliverability  free, needs DNS — does the mail domain accept mail?

Every function returns a Result: the cleaned value, whether it is usable, a
machine status, and a human reason. Nothing is ever silently dropped, and
nothing is ever labelled verified without a check behind it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from . import normalize

# --------------------------------------------------------------------------
# Result type
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Result:
    value: str            # cleaned/normalized value ("" if unusable)
    raw: str              # exactly what came in
    ok: bool              # safe to use?
    status: str           # machine-readable outcome
    reason: str = ""      # human-readable, shown in the rejected-rows report

    def __bool__(self) -> bool:
        return self.ok


def _ok(value, raw, status):
    return Result(value=value, raw=raw, ok=True, status=status)


def _bad(raw, status, reason):
    return Result(value="", raw=raw, ok=False, status=status, reason=reason)


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Addresses that are real but are not a person or a business enquiry route.
ROLE_LOCALPARTS = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "postmaster",
    "abuse", "mailer-daemon", "bounce", "bounces", "webmaster",
}

# Obvious placeholders copied from templates and tutorials.
PLACEHOLDER_LOCALPARTS = {
    "example", "test", "testing", "demo", "sample", "youremail",
    "your-email", "email", "your_name", "yourname", "username", "user",
    "name", "abc", "xyz", "asdf", "aaa", "someone", "somebody",
}
PLACEHOLDER_DOMAINS = {
    "example.com", "example.org", "example.net", "example.in", "test.com",
    "domain.com", "yourdomain.com", "mydomain.com", "email.com",
    "sentry.io", "wixpress.com", "godaddy.com", "squarespace.com",
}
DISPOSABLE_DOMAINS = {
    "mailinator.com", "10minutemail.com", "guerrillamail.com",
    "yopmail.com", "tempmail.com", "throwawaymail.com", "trashmail.com",
    "sharklasers.com", "getnada.com", "maildrop.cc",
}
# File extensions that a greedy regex picks up out of CSS/JS as "addresses".
ASSET_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".css", ".js", ".json", ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".pdf",
)
# Generic mailboxes. Perfectly usable for B2B enquiries — just flagged so the
# quality score can prefer a named person's address.
GENERIC_LOCALPARTS = {
    "info", "contact", "sales", "enquiry", "enquiries", "inquiry",
    "office", "admin", "support", "marketing", "mail", "hello", "care",
    "help", "service", "services", "export", "purchase", "accounts",
}


def validate_email(raw: str, check_mx: bool = False) -> Result:
    """
    Tier 1 + 2 always. Tier 3 (MX lookup) when check_mx is True.

    Status is one of:
        MX_OK        domain really accepts mail          (ok)
        SYNTAX_OK    looks right, deliverability unknown (ok)
        NO_MX        domain does not accept mail         (rejected)
        SYNTAX_BAD / PLACEHOLDER / DISPOSABLE / ROLE / ASSET / EMPTY
    """
    value = normalize.normalize_email(raw)
    if not value:
        return _bad(raw, "EMPTY", "no email")

    if value.endswith(ASSET_SUFFIXES):
        return _bad(raw, "ASSET", "matched a file name, not an address")

    if not EMAIL_RE.match(value):
        return _bad(raw, "SYNTAX_BAD", "not a valid email address")

    local, _, domain = value.rpartition("@")

    if local in ROLE_LOCALPARTS:
        return _bad(raw, "ROLE", f"'{local}@' does not accept replies")
    if domain in DISPOSABLE_DOMAINS:
        return _bad(raw, "DISPOSABLE", "disposable mail domain")
    if domain in PLACEHOLDER_DOMAINS or local in PLACEHOLDER_LOCALPARTS:
        return _bad(raw, "PLACEHOLDER", "placeholder address")
    # Repeated-character local parts like "aaaa@" or "xxxx@"
    if len(set(local)) == 1 and len(local) > 2:
        return _bad(raw, "PLACEHOLDER", "placeholder address")

    if check_mx:
        if domain_accepts_mail(domain):
            return _ok(value, raw, "MX_OK")
        return _bad(raw, "NO_MX", f"{domain} does not accept mail")

    return _ok(value, raw, "SYNTAX_OK")


def is_generic_email(value: str) -> bool:
    """True for info@ / sales@ style mailboxes rather than a named person."""
    local = normalize.normalize_email(value).partition("@")[0]
    return local in GENERIC_LOCALPARTS


@lru_cache(maxsize=4096)
def domain_accepts_mail(domain: str) -> bool:
    """
    Tier 3: does this domain publish MX records (or at least an A record that
    could receive mail)?

    Free, no API key. Requires dnspython; without it we cannot prove the
    domain is bad, so we answer True rather than reject good leads.
    """
    if not domain:
        return False
    try:
        import dns.resolver  # type: ignore
    except ImportError:
        return True

    resolver = dns.resolver.Resolver()
    resolver.lifetime = 5.0
    resolver.timeout = 5.0
    try:
        answers = resolver.resolve(domain, "MX")
        if any(str(r.exchange).strip(".") for r in answers):
            return True
    except Exception:
        pass
    # RFC 5321: with no MX, the A record is the mail exchanger.
    try:
        resolver.resolve(domain, "A")
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# Phone
# --------------------------------------------------------------------------

# Numbers that are structurally valid but cannot be a real contact.
JUNK_PHONE_RE = (
    re.compile(r"^(\d)\1{9}$"),          # 9999999999
    re.compile(r"^1234567890$"),
    re.compile(r"^123456789$"),        # "0123456789" with the zero stripped
    re.compile(r"^(?:0?1)?23456789\d?$"),
    re.compile(r"^9876543210$"),         # the textbook example number
    re.compile(r"^(?:19|20)\d{2}(?:19|20)\d{2}\d{2}$"),  # dates run together
)


def validate_phone(raw: str) -> Result:
    """
    India-aware phone validation.

    Status: MOBILE_OK | LANDLINE_OK | TOLLFREE_OK | INTL_OK
            FORMAT_BAD | JUNK | EMPTY
    """
    if not normalize.clean_text(raw):
        return _bad(raw, "EMPTY", "no phone number")

    # Check for a placeholder before checking the length: "0123456789" is a
    # placeholder, and rejecting it as "wrong length" would hide the real
    # reason from the user.
    probe = re.sub(r"\D", "", str(raw))
    if probe.startswith("00"):
        probe = probe[2:]
    if probe.startswith("91") and len(probe) > 10:
        probe = probe[2:]
    probe = probe.lstrip("0")
    for pattern in JUNK_PHONE_RE:
        if pattern.match(probe):
            return _bad(raw, "JUNK", "placeholder or sequential number")

    value = normalize.normalize_phone(raw)
    if not value:
        return _bad(raw, "FORMAT_BAD", "not a valid phone number")

    digits = value.lstrip("+")

    if digits.startswith("91"):
        national = digits[2:]
        if national.startswith("1800"):
            return _ok(value, raw, "TOLLFREE_OK")
        if len(national) != 10:
            return _bad(raw, "FORMAT_BAD", "Indian numbers must be 10 digits")
        if national[0] in "6789":
            return _ok(value, raw, "MOBILE_OK")
        if national[0] in "234578":
            return _ok(value, raw, "LANDLINE_OK")
        return _bad(raw, "FORMAT_BAD", "not a valid Indian number")

    return _ok(value, raw, "INTL_OK")


def is_mobile(value: str) -> bool:
    digits = normalize.normalize_phone(value).lstrip("+")
    return digits.startswith("91") and len(digits) == 12 and digits[2] in "6789"


# --------------------------------------------------------------------------
# Website
# --------------------------------------------------------------------------

# Domains that host other companies' listings, never a company of their own.
DIRECTORY_DOMAINS = {
    "indiamart.com", "justdial.com", "tradeindia.com", "exportersindia.com",
    "sulekha.com", "yellowpages.in", "indiacom.com", "zaubacorp.com",
    "tofler.in", "instafinancials.com", "thecompanycheck.com",
    "quickcompany.in", "falconebiz.com", "indiafilings.com",
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "wikipedia.org", "pinterest.com", "blogspot.com",
    "wordpress.com", "medium.com", "google.com", "bing.com", "amazon.in",
    "flipkart.com", "alibaba.com", "made-in-china.com", "scribd.com",
    "slideshare.net", "issuu.com", "glassdoor.co.in", "naukri.com",
    "indeed.com", "quora.com", "reddit.com",
}

# Title patterns that mean the page lists companies rather than being one.
LISTICLE_RE = re.compile(
    r"\b(top|best|list of|top-?\d+|\d+\s+best|directory|manufacturers? in|"
    r"suppliers? in|dealers? in|companies in|near me|price list|"
    r"how to|what is|guide to|vs\.?)\b",
    re.IGNORECASE,
)


def validate_website(raw: str) -> Result:
    """Status: URL_OK | DIRECTORY | FORMAT_BAD | EMPTY"""
    if not normalize.clean_text(raw):
        return _bad(raw, "EMPTY", "no website")

    url = normalize.normalize_url(raw)
    domain = normalize.normalize_domain(url)
    if not url or not domain:
        return _bad(raw, "FORMAT_BAD", "not a valid website address")

    if is_directory_domain(domain):
        return _bad(raw, "DIRECTORY", f"{domain} is a directory, not a company")

    return _ok(url, raw, "URL_OK")


# The distinctive label of each known directory: "indiamart", "wikipedia"…
_DIRECTORY_ROOTS = {known.split(".")[0] for known in DIRECTORY_DOMAINS}


def is_directory_domain(domain: str) -> bool:
    """
    True when this host belongs to a directory, marketplace or social site.

    Matches the domain itself, any subdomain of it (en.wikipedia.org), and
    regional variants (indiamart.co.in, facebook.com.br).
    """
    domain = normalize.normalize_domain(domain) or str(domain).strip().lower()
    if not domain:
        return False

    for known in DIRECTORY_DOMAINS:
        if domain == known or domain.endswith("." + known):
            return True

    # Every label except the final TLD: catches both a subdomain
    # ("en.wikipedia.org") and a different country suffix ("indiamart.co.in").
    return any(label in _DIRECTORY_ROOTS for label in domain.split(".")[:-1])


def looks_like_listicle(title: str) -> bool:
    """True when a page title advertises a list of companies, not a company."""
    return bool(LISTICLE_RE.search(normalize.clean_text(title)))


# --------------------------------------------------------------------------
# Company name
# --------------------------------------------------------------------------

def validate_company_name(raw: str) -> Result:
    """
    Status: NAME_OK | LISTICLE | TOO_SHORT | NO_LETTERS | EMPTY

    Rejecting listicle-style titles is what stops rows like
    "Top 10 Sponge Iron Manufacturers in West Bengal" becoming companies.
    """
    value = normalize.strip_title_noise(raw)
    if not value:
        return _bad(raw, "EMPTY", "no company name")
    if looks_like_listicle(value):
        return _bad(raw, "LISTICLE", "page title is a list, not a company name")
    if len(value) < 3:
        return _bad(raw, "TOO_SHORT", "company name too short")
    if not re.search(r"[A-Za-z]{2,}", value):
        return _bad(raw, "NO_LETTERS", "company name has no letters")
    if not normalize.normalize_name(value):
        return _bad(raw, "TOO_SHORT", "company name is only a legal suffix")
    return _ok(value, raw, "NAME_OK")


# --------------------------------------------------------------------------
# Statutory identifiers
# --------------------------------------------------------------------------

# L/U + 5-digit industry code + 2-letter state + 4-digit year
# + 3-letter ownership + 6-digit registration number
CIN_RE = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]$")

# 01-38 are the states and union territories; 97 is "Other Territory" and
# 99 is Centre Jurisdiction. Both are genuine, so neither is rejected.
GST_STATE_CODES = {f"{n:02d}" for n in range(1, 39)} | {"97", "99"}
CIN_STATE_CODES = {
    "AP", "AR", "AS", "BR", "CH", "CT", "DL", "GA", "GJ", "HR", "HP", "JH",
    "JK", "KA", "KL", "MP", "MH", "MN", "ML", "MZ", "NL", "OR", "PB", "PY",
    "RJ", "SK", "TN", "TG", "TR", "UP", "UT", "UR", "WB", "AN", "DN", "DD",
    "LD", "CG", "OD",
}
_GST_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def validate_cin(raw: str) -> Result:
    """Status: CIN_OK | CIN_BAD | EMPTY (structure only — MCA has no checksum)"""
    value = normalize.normalize_identifier(raw)
    if not value:
        return _bad(raw, "EMPTY", "no CIN")
    if not CIN_RE.match(value):
        return _bad(raw, "CIN_BAD", "not a valid 21-character CIN")
    if value[6:8] not in CIN_STATE_CODES:
        return _bad(raw, "CIN_BAD", f"'{value[6:8]}' is not a state code")
    year = int(value[8:12])
    if not 1850 <= year <= 2100:
        return _bad(raw, "CIN_BAD", f"incorporation year {year} is implausible")
    return _ok(value, raw, "CIN_OK")


def gstin_checksum(first14: str) -> str:
    """
    The 15th character of a GSTIN, per the official base-36 weighted algorithm.
    Weights alternate 1, 2 across the first 14 characters.
    """
    total = 0
    for index, char in enumerate(first14):
        code = _GST_ALPHABET.index(char)
        product = code * (2 if index % 2 else 1)
        total += product // 36 + product % 36
    return _GST_ALPHABET[(36 - total % 36) % 36]


def validate_gstin(raw: str) -> Result:
    """Status: GSTIN_OK | GSTIN_BAD | EMPTY — includes the real checksum."""
    value = normalize.normalize_identifier(raw)
    if not value:
        return _bad(raw, "EMPTY", "no GSTIN")
    if not GSTIN_RE.match(value):
        return _bad(raw, "GSTIN_BAD", "not a valid 15-character GSTIN")
    if value[:2] not in GST_STATE_CODES:
        return _bad(raw, "GSTIN_BAD", f"'{value[:2]}' is not a GST state code")
    if gstin_checksum(value[:14]) != value[14]:
        return _bad(raw, "GSTIN_BAD", "GSTIN checksum does not match")
    return _ok(value, raw, "GSTIN_OK")


def validate_pin(raw: str) -> Result:
    """Status: PIN_OK | PIN_BAD | EMPTY"""
    if not normalize.clean_text(raw):
        return _bad(raw, "EMPTY", "no PIN code")
    value = normalize.normalize_pin(raw)
    if not value:
        return _bad(raw, "PIN_BAD", "not a valid 6-digit PIN code")
    return _ok(value, raw, "PIN_OK")


def state_from_pin(pin: str) -> str:
    """
    Broad state guess from a PIN prefix — used only to flag a mismatch
    between a stated state and its PIN, never to overwrite the state.
    """
    value = normalize.normalize_pin(pin)
    if not value:
        return ""
    prefixes = {
        "70": "West Bengal", "71": "West Bengal", "72": "West Bengal",
        "73": "West Bengal", "74": "West Bengal",
        "75": "Odisha", "76": "Odisha", "77": "Odisha",
        "78": "Assam", "79": "Arunachal Pradesh",
        "80": "Bihar", "81": "Bihar", "82": "Bihar",
        "83": "Jharkhand", "84": "Bihar", "85": "Bihar",
    }
    return prefixes.get(value[:2], "")
