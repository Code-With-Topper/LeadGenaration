"""
The one shape every lead takes before it touches the database.

The scraper and the importer both build a CleanRecord. Everything downstream —
matching, merging, scoring, saving — only ever sees a CleanRecord, so the two
entry paths cannot drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import normalize, validation


@dataclass
class CleanRecord:
    # --- identity -------------------------------------------------------
    company_name: str = ""
    normalized_name: str = ""
    industry: str = ""
    company_type: str = ""

    # --- reachability ---------------------------------------------------
    website: str = ""
    normalized_domain: str = ""
    company_email: str = ""
    company_phone: str = ""
    email_status: str = "UNKNOWN"
    phone_status: str = "UNKNOWN"

    # --- statutory ------------------------------------------------------
    cin: str = ""
    gstin: str = ""

    # --- location -------------------------------------------------------
    plant_name: str = ""
    plant_address: str = ""
    city: str = ""
    district: str = ""
    state: str = ""
    pin_code: str = ""

    # --- links ----------------------------------------------------------
    linkedin_url: str = ""
    google_maps_url: str = ""

    # --- contact person -------------------------------------------------
    contact_name: str = ""
    designation: str = ""
    department: str = ""
    contact_email: str = ""
    contact_mobile: str = ""
    contact_direct_phone: str = ""

    # --- extra reachability found while crawling ------------------------
    extra_emails: list[str] = field(default_factory=list)
    extra_phones: list[str] = field(default_factory=list)

    # --- provenance -----------------------------------------------------
    lead_source: str = ""
    source_query: str = ""
    source_url: str = ""
    remarks: str = ""

    # --- outcome of validation -----------------------------------------
    rejected: bool = False
    reject_reason: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def has_reachability(self) -> bool:
        """
        A lead nobody can be reached through is not a lead.

        A website counts: an enquiry form or a contact page is a real way in,
        and the number or address can be filled in later. Rejecting these
        would throw away companies that are genuinely worth chasing.
        """
        return bool(
            self.company_email or self.company_phone
            or self.contact_email or self.contact_mobile
            or self.normalized_domain
        )

    def reject(self, reason: str) -> "CleanRecord":
        self.rejected = True
        self.reject_reason = reason
        return self


def build_record(raw: dict, *, check_mx: bool = False,
                 require_reachability: bool = True) -> CleanRecord:
    """
    Normalize and validate one raw dict into a CleanRecord.

    `raw` keys are the field names in CleanRecord. Missing keys are fine.
    A record that cannot be used is returned with rejected=True and a reason
    written in plain language, so it can be shown in the rejected-rows report
    rather than disappearing.
    """
    record = CleanRecord()
    get = lambda key: normalize.clean_text(raw.get(key, ""))

    # --- company name: the hard gate --------------------------------
    name_result = validation.validate_company_name(raw.get("company_name", ""))
    if not name_result.ok:
        return record.reject(name_result.reason)
    record.company_name = name_result.value
    record.normalized_name = normalize.normalize_name(name_result.value)

    record.industry = get("industry")
    record.company_type = get("company_type")

    # --- website ----------------------------------------------------
    site_result = validation.validate_website(raw.get("website", ""))
    if site_result.ok:
        record.website = site_result.value
        record.normalized_domain = normalize.normalize_domain(site_result.value)
    elif site_result.status == "DIRECTORY":
        return record.reject(site_result.reason)
    elif site_result.status == "FORMAT_BAD":
        record.warnings.append(f"website dropped: {site_result.reason}")

    # --- email ------------------------------------------------------
    email_result = validation.validate_email(
        raw.get("company_email", ""), check_mx=check_mx)
    if email_result.ok:
        record.company_email = email_result.value
        record.email_status = email_result.status
    else:
        record.email_status = email_result.status
        if email_result.status != "EMPTY":
            record.warnings.append(f"email dropped: {email_result.reason}")

    # --- phone ------------------------------------------------------
    phone_result = validation.validate_phone(raw.get("company_phone", ""))
    if phone_result.ok:
        record.company_phone = phone_result.value
        record.phone_status = phone_result.status
    else:
        record.phone_status = phone_result.status
        if phone_result.status != "EMPTY":
            record.warnings.append(f"phone dropped: {phone_result.reason}")

    # --- statutory identifiers --------------------------------------
    cin_result = validation.validate_cin(raw.get("cin", ""))
    if cin_result.ok:
        record.cin = cin_result.value
    elif cin_result.status != "EMPTY":
        record.warnings.append(f"CIN dropped: {cin_result.reason}")

    gstin_result = validation.validate_gstin(raw.get("gstin", ""))
    if gstin_result.ok:
        record.gstin = gstin_result.value
    elif gstin_result.status != "EMPTY":
        record.warnings.append(f"GSTIN dropped: {gstin_result.reason}")

    # --- location ---------------------------------------------------
    record.plant_name = get("plant_name")
    record.plant_address = get("plant_address")
    record.city = get("city").title() if get("city").isupper() else get("city")
    record.district = get("district")
    record.state = get("state")

    pin_result = validation.validate_pin(raw.get("pin_code", ""))
    if pin_result.ok:
        record.pin_code = pin_result.value
        guessed = validation.state_from_pin(pin_result.value)
        if guessed and record.state and guessed.lower() != record.state.lower():
            record.warnings.append(
                f"PIN {pin_result.value} looks like {guessed}, not {record.state}")
    elif pin_result.status != "EMPTY":
        record.warnings.append(f"PIN dropped: {pin_result.reason}")

    # --- links ------------------------------------------------------
    linkedin = normalize.normalize_url(raw.get("linkedin_url", ""))
    if "linkedin.com" in linkedin:
        record.linkedin_url = linkedin
    record.google_maps_url = normalize.clean_text(raw.get("google_maps_url", ""))

    # --- contact person ---------------------------------------------
    record.contact_name = get("contact_name")
    record.designation = get("designation")
    record.department = get("department")

    contact_email = validation.validate_email(
        raw.get("contact_email", ""), check_mx=check_mx)
    if contact_email.ok:
        record.contact_email = contact_email.value
    elif contact_email.status != "EMPTY":
        record.warnings.append(f"contact email dropped: {contact_email.reason}")

    contact_mobile = validation.validate_phone(raw.get("contact_mobile", ""))
    if contact_mobile.ok:
        record.contact_mobile = contact_mobile.value
    elif contact_mobile.status != "EMPTY":
        record.warnings.append(f"contact mobile dropped: {contact_mobile.reason}")

    direct = validation.validate_phone(raw.get("contact_direct_phone", ""))
    if direct.ok:
        record.contact_direct_phone = direct.value

    # --- extras found while crawling --------------------------------
    for value in raw.get("extra_emails") or []:
        result = validation.validate_email(value, check_mx=check_mx)
        if result.ok and result.value != record.company_email:
            record.extra_emails.append(result.value)
    for value in raw.get("extra_phones") or []:
        result = validation.validate_phone(value)
        if result.ok and result.value != record.company_phone:
            record.extra_phones.append(result.value)

    # Promote an extra to primary when the primary is missing.
    if not record.company_email and record.extra_emails:
        record.company_email = record.extra_emails.pop(0)
        record.email_status = "MX_OK" if check_mx else "SYNTAX_OK"
    if not record.company_phone and record.extra_phones:
        record.company_phone = record.extra_phones.pop(0)
        record.phone_status = "PLAUSIBLE"

    # --- provenance -------------------------------------------------
    record.lead_source = get("lead_source")
    record.source_query = get("source_query")
    record.source_url = normalize.normalize_url(raw.get("source_url", ""))
    record.remarks = get("remarks")

    if require_reachability and not record.has_reachability:
        return record.reject("no usable email or phone number")

    return record
