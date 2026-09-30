"""
Duplicate detection, in three tiers.

    Tier 1  hard keys      CIN, GSTIN, domain, email, phone  → auto-merge
    Tier 2  scored match    name + PIN + city + phone tail    → merge or review
    Tier 3  human review    anything in between

Candidates are always restricted by a blocking key first, so the cost grows
with the size of a neighbourhood rather than with the square of the database.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from django.db.models import Q

from . import normalize
from .record import CleanRecord

# Score at or above which two records are certainly the same company.
AUTO_MERGE_SCORE = 85
# Score below which they are certainly different.
REVIEW_FLOOR_SCORE = 60

NEW, MERGE, REVIEW = "NEW", "MERGE", "REVIEW"


@dataclass
class MatchResult:
    decision: str            # NEW | MERGE | REVIEW
    company = None           # leads.models.Company or None
    score: int = 100
    reason: str = ""
    tier: str = ""


# --------------------------------------------------------------------------
# Similarity
# --------------------------------------------------------------------------

def token_set_ratio(left: str, right: str) -> float:
    """
    Order-insensitive string similarity in 0..1.

    "sd steel durgapur" vs "durgapur sd steel" scores 1.0, which matters
    because company names are written in any order across sources.
    """
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0

    left_tokens = set(left.split())
    right_tokens = set(right.split())
    shared = left_tokens & right_tokens

    # Compare the shared core against each full string, take the best.
    core = " ".join(sorted(shared))
    left_full = " ".join(sorted(shared)) + " " + " ".join(sorted(left_tokens - shared))
    right_full = " ".join(sorted(shared)) + " " + " ".join(sorted(right_tokens - shared))

    candidates = [
        SequenceMatcher(None, left_full.strip(), right_full.strip()).ratio(),
    ]
    if core.strip():
        candidates.append(SequenceMatcher(None, core, left_full.strip()).ratio())
        candidates.append(SequenceMatcher(None, core, right_full.strip()).ratio())
    return max(candidates)


def phone_tail(phone: str) -> str:
    """Last 8 digits — survives differences in country and STD prefixes."""
    digits = normalize.normalize_phone(phone).lstrip("+")
    return digits[-8:] if len(digits) >= 8 else ""


# --------------------------------------------------------------------------
# Tier 1 — hard keys
# --------------------------------------------------------------------------

def find_by_hard_key(record: CleanRecord, exclude_id: int | None = None):
    """
    Return (company, reason) when a statutory or unique contact key matches.

    These keys identify a company beyond reasonable doubt, so a hit here is
    merged without asking anyone.
    """
    from leads.models import Company

    checks = (
        ("cin", record.cin, "same CIN"),
        ("gstin", record.gstin, "same GSTIN"),
        ("normalized_domain", record.normalized_domain, "same website domain"),
        ("normalized_email", record.company_email, "same email address"),
        ("normalized_phone", record.company_phone, "same phone number"),
    )
    for field_name, value, reason in checks:
        if not value:
            continue
        queryset = Company.objects.filter(**{field_name: value})
        if exclude_id:
            queryset = queryset.exclude(id=exclude_id)
        company = queryset.first()
        if company:
            return company, reason
    return None, ""


# --------------------------------------------------------------------------
# Tier 2 — blocked, scored match
# --------------------------------------------------------------------------

def candidate_queryset(record: CleanRecord, exclude_id: int | None = None):
    """
    Narrow the search space before scoring.

    A company is only ever compared against others that share a PIN, a city,
    a domain, or the first significant word of its name.
    """
    from leads.models import Company

    blocks = Q()
    matched_any = False

    first_token = record.normalized_name.split(" ")[0] if record.normalized_name else ""
    if len(first_token) >= 3:
        blocks |= Q(normalized_name__startswith=first_token)
        matched_any = True
    if record.pin_code:
        blocks |= Q(plants__pin_code=record.pin_code)
        matched_any = True
    if record.city:
        blocks |= Q(plants__city__iexact=record.city)
        matched_any = True
    if record.normalized_domain:
        blocks |= Q(normalized_domain=record.normalized_domain)
        matched_any = True

    if not matched_any:
        return Company.objects.none()

    queryset = Company.objects.filter(blocks).distinct()
    if exclude_id:
        queryset = queryset.exclude(id=exclude_id)
    # A hard cap keeps a pathological block (e.g. every company in Kolkata)
    # from turning one save into a full table scan.
    return queryset.prefetch_related("plants")[:200]


def score_pair(record: CleanRecord, company) -> tuple[int, list[str]]:
    """
    How strongly does this record look like an existing company?

        normalized name similarity ... up to 50
        identical normalized name ...... +10   (bonus, see below)
        same PIN code ................... 20
        same city ....................... 10
        same phone tail ................. 20

    The bonus matters: two names are only *identical* after normalisation once
    case, punctuation, "M/s" and the legal form have all been accounted for.
    "SD Steel Pvt Ltd" and "S. D. Steel Private Limited" at the same PIN code
    are the same company, and asking a human to confirm that every time would
    make the review queue useless.
    """
    score = 0
    reasons: list[str] = []

    similarity = token_set_ratio(record.normalized_name, company.normalized_name or "")
    name_points = int(round(similarity * 50))
    score += name_points
    if name_points >= 35:
        reasons.append(f"name {int(similarity * 100)}% similar")

    exact_name = (record.normalized_name
                  and record.normalized_name == (company.normalized_name or ""))
    if exact_name:
        score += 10
        reasons[-1:] = ["same company name"]

    plants = list(company.plants.all())

    if record.pin_code and any(p.pin_code == record.pin_code for p in plants):
        score += 20
        reasons.append(f"same PIN {record.pin_code}")

    if record.city and any(
        (p.city or "").lower() == record.city.lower() for p in plants
    ):
        score += 10
        reasons.append(f"same city {record.city}")

    tail = phone_tail(record.company_phone)
    if tail and tail == phone_tail(company.normalized_phone or ""):
        score += 20
        reasons.append("same phone")

    return min(score, 100), reasons


def find_by_similarity(record: CleanRecord, exclude_id: int | None = None):
    """Best-scoring candidate above the review floor, or (None, 0, '')."""
    best_company, best_score, best_reasons = None, 0, []
    for company in candidate_queryset(record, exclude_id=exclude_id):
        score, reasons = score_pair(record, company)
        if score > best_score:
            best_company, best_score, best_reasons = company, score, reasons
    if best_score < REVIEW_FLOOR_SCORE:
        return None, 0, ""
    return best_company, best_score, ", ".join(best_reasons)


# --------------------------------------------------------------------------
# The decision
# --------------------------------------------------------------------------

def decide(record: CleanRecord, exclude_id: int | None = None) -> MatchResult:
    """
    Classify a record against what is already stored.

        NEW     nothing like it exists
        MERGE   certainly the same company — safe to merge automatically
        REVIEW  probably the same company — a human should confirm
    """
    company, reason = find_by_hard_key(record, exclude_id=exclude_id)
    if company:
        result = MatchResult(decision=MERGE, score=100, reason=reason, tier="hard-key")
        result.company = company
        return result

    company, score, reason = find_by_similarity(record, exclude_id=exclude_id)
    if company:
        decision = MERGE if score >= AUTO_MERGE_SCORE else REVIEW
        result = MatchResult(decision=decision, score=score, reason=reason,
                             tier="similarity")
        result.company = company
        return result

    return MatchResult(decision=NEW, score=0, reason="no similar company found",
                       tier="none")


# --------------------------------------------------------------------------
# Within-batch dedupe (used by the importer before anything is written)
# --------------------------------------------------------------------------

class BatchIndex:
    """
    In-memory index of the rows already seen in the current file.

    Without this, a CSV containing the same company three times would create
    three companies, because none of them is in the database yet when the
    others are checked.
    """

    def __init__(self):
        self._keys: dict[str, int] = {}   # hard key -> row number
        self._names: list[tuple[str, str, int]] = []  # (norm name, pin, row)

    def seen(self, record: CleanRecord) -> tuple[bool, int, str]:
        """Return (is_duplicate, first_row_number, reason)."""
        for value, label in (
            (record.cin, "CIN"),
            (record.gstin, "GSTIN"),
            (record.normalized_domain, "website"),
            (record.company_email, "email"),
            (record.company_phone, "phone"),
        ):
            if value and value in self._keys:
                return True, self._keys[value], f"same {label} as row {self._keys[value]}"

        for name, pin, row in self._names:
            if not name or not record.normalized_name:
                continue
            same_place = (not pin or not record.pin_code or pin == record.pin_code)
            if same_place and token_set_ratio(record.normalized_name, name) >= 0.92:
                return True, row, f"same company name as row {row}"
        return False, 0, ""

    def add(self, record: CleanRecord, row_number: int) -> None:
        for value in (record.cin, record.gstin, record.normalized_domain,
                      record.company_email, record.company_phone):
            if value:
                self._keys.setdefault(value, row_number)
        if record.normalized_name:
            self._names.append((record.normalized_name, record.pin_code, row_number))
