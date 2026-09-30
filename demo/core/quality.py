"""
Data quality scoring.

The score is stored on the Company, not recomputed per page load, so list
pages and the dashboard stay fast at 10,000 leads.

It answers one question for the user: *can I actually act on this lead today?*
"""
from __future__ import annotations

# Points are weighted by how much each field helps someone make a sale.
WEIGHTS = {
    "name_from_site": 20,   # a real company name, not a search-result title
    "address_and_pin": 15,
    "phone": 15,
    "email": 20,
    "website": 10,
    "statutory": 15,        # CIN or GSTIN
    "named_contact": 5,
}

BANDS = (
    (80, "A", "Ready to contact"),
    (60, "B", "Usable"),
    (40, "C", "Thin"),
    (0,  "D", "Needs work"),
)


def score_company(company) -> int:
    """0-100. Reads only the company and its related plants and contacts."""
    total = 0

    if company.company_name and company.identity_status != "TITLE_GUESS":
        total += WEIGHTS["name_from_site"]

    plant = company.plants.first()
    if plant and plant.pin_code and (plant.plant_address or plant.city):
        total += WEIGHTS["address_and_pin"]
    elif plant and (plant.city or plant.plant_address):
        total += WEIGHTS["address_and_pin"] // 2

    if company.normalized_phone:
        total += WEIGHTS["phone"]

    if company.normalized_email:
        # A domain proven to accept mail is worth more than one we only parsed.
        total += WEIGHTS["email"] if company.email_status == "MX_OK" \
            else WEIGHTS["email"] * 3 // 4

    if company.normalized_domain:
        total += WEIGHTS["website"]

    if company.cin or company.gstin:
        total += WEIGHTS["statutory"]

    if company.contacts.exclude(name="").exists():
        total += WEIGHTS["named_contact"]

    return min(total, 100)


def band(score: int) -> tuple[str, str]:
    """('A', 'Ready to contact') for a score."""
    for threshold, letter, label in BANDS:
        if score >= threshold:
            return letter, label
    return "D", "Needs work"


def missing_fields(company) -> list[str]:
    """Plain-language list of what would raise this company's score."""
    gaps = []
    plant = company.plants.first()
    if not company.normalized_email:
        gaps.append("email")
    if not company.normalized_phone:
        gaps.append("phone")
    if not company.normalized_domain:
        gaps.append("website")
    if not plant or not plant.pin_code:
        gaps.append("PIN code")
    if not plant or not plant.plant_address:
        gaps.append("address")
    if not (company.cin or company.gstin):
        gaps.append("CIN/GSTIN")
    if not company.contacts.exclude(name="").exists():
        gaps.append("contact person")
    return gaps


def refresh(company, save: bool = True) -> int:
    """Recompute and store the score. Returns the new score."""
    score = score_company(company)
    if company.data_quality_score != score:
        company.data_quality_score = score
        if save:
            company.save(update_fields=["data_quality_score", "updated_at"])
    return score
