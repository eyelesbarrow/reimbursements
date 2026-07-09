
import sqlite3
import math
import re
from datetime import date, datetime
from typing import Optional, Tuple
from constants import Constants
from models import (
    PatientCase,
    DrugPricing,
    CoverageRule,   
    AdminFee,
    DenialCode,
    Verdict,
    Appeal,
)

"""
calc_engine.py

Coverage verdict and reimbursement calculator for buy-and-bill drugs.
Built for the Field Reimbursement Intelligence prototype.

Architecture:
  Layer A — Reference data (payment limits, PFS, crosswalk)
  Layer B — Coverage rules (hand-curated policies)
  Layer C — Calc engine (this file)
  Layer D — Denial translator + appeal generator
"""


# ============================================================================
# Database Access Layer
# ============================================================================

def _get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Get a SQLite connection with row factory set."""
    path = db_path or Constants.DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_drug_pricing(hcpcs: str, db_path: Optional[str] = None) -> Optional[DrugPricing]:
    """
    Fetch drug pricing from payment_limits table, enriched with crosswalk data.
    
    Your payment_limits_072026 table has:
      - HCPCSCode (String/Integer) — the HCPCS code
      - ShortDescription — drug name
      - HCPCSCodeDosage — billing unit size (e.g., '10 mg')
      - PaymentLimit — the ASP payment limit per unit
    
    Your crosswalk table has:
      - _2026_CODE — the HCPCS code
      - DrugName — brand name
      - LabelerName — manufacturer
      - NDC — National Drug Codes
    """
    conn = _get_connection(db_path)
    
    # Fetch payment limit
    row = conn.execute(
        """SELECT HCPCSCode, ShortDescription, HCPCSCodeDosage, PaymentLimit
           FROM payment_limits_072026
           WHERE CAST(HCPCSCode AS TEXT) = ?""",
        (hcpcs.upper(),)
    ).fetchone()
    
    if not row:
        conn.close()
        return None
    
    # Parse dosage from string like '10 mg' or '10'
    dosage_str = str(row["HCPCSCodeDosage"] or "10")
    dosage_match = re.search(r'([\d.]+)', dosage_str)
    dosage_mg = int(float(dosage_match.group(1))) if dosage_match else 10
    
    payment_limit = float(row["PaymentLimit"])
    
    # Determine uplift percentage
    # Q-codes are biosimilars; J-codes are originators
    is_biosimilar = hcpcs.upper().startswith("Q")
    uplift_pct = Constants.ASP_UPLIFT_BIOSIMILAR if is_biosimilar else Constants.ASP_UPLIFT_ORIGINATOR
    asp_implied = round(payment_limit / (1 + uplift_pct), 4)
    
    # Fetch crosswalk data
    crosswalk_rows = conn.execute(
        """SELECT DISTINCT DrugName, LabelerName, NDC
           FROM asp_ndc_hscp_crosswalk_072026
           WHERE CAST("_2026_CODE" AS TEXT) = ?""",
        (hcpcs.upper(),)
    ).fetchall()
    
    drug_name = crosswalk_rows[0]["DrugName"] if crosswalk_rows else None
    labeler_name = crosswalk_rows[0]["LabelerName"] if crosswalk_rows else None
    ndcs = [r["NDC"] for r in crosswalk_rows if r["NDC"]]
    
    conn.close()
    
    return DrugPricing(
        hcpcs=hcpcs.upper(),
        short_description=str(row["ShortDescription"] or ""),
        dosage_mg=dosage_mg,
        payment_limit=payment_limit,
        uplift_pct=uplift_pct,
        asp_implied=asp_implied,
        effective_quarter="2026Q3",
        biosimilar=is_biosimilar,
        labeler_name=labeler_name,
        drug_name=drug_name,
        ndcs=ndcs,
    )


def fetch_coverage_rule(
    payer_id: str,
    hcpcs: str,
    line_of_business: str,
    db_path: Optional[str] = None
) -> Optional[CoverageRule]:
    """
    Fetch coverage policy from coverage_policy table.
    
    Your coverage_policy table stores booleans as TEXT ('True'/'False').
    """
    conn = _get_connection(db_path)
    
    row = conn.execute(
        """SELECT drug_id, payer_id, line_of_business, hcpcs, pa_required, preferred,
                  preferred_alt_hcpcs, site_of_care_pref, effective_start, effective_end,
                  policy_url, last_verified, notes
           FROM coverage_policy
           WHERE payer_id = ? AND hcpcs = ? AND line_of_business = ?""",
        (payer_id, hcpcs.upper(), line_of_business)
    ).fetchone()
    
    conn.close()
    
    if not row:
        return None
    
    def _parse_bool(val) -> bool:
        if val is None:
            return False
        if isinstance(val, bool):
            return val
        if isinstance(val, int):
            return bool(val)
        return str(val).strip().lower() in ("true", "1", "yes", "t")
    
    def _parse_date(val) -> Optional[date]:
        if not val:
            return None
        try:
            return datetime.strptime(str(val).strip(), "%Y-%m-%d").date()
        except ValueError:
            return None
    
    def _clean_hcpcs(val) -> Optional[str]:
        if not val:
            return None
        val = str(val).strip().upper()
        return None if val in ("NULL", "NONE", "") else val
    
    return CoverageRule(
        payer_id=str(row["payer_id"]),
        line_of_business=str(row["line_of_business"]),
        hcpcs=str(row["hcpcs"]),
        pa_required=_parse_bool(row["pa_required"]),
        preferred=_parse_bool(row["preferred"]),
        preferred_alt_hcpcs=_clean_hcpcs(row["preferred_alt_hcpcs"]),
        site_of_care_pref=str(row["site_of_care_pref"]) if row["site_of_care_pref"] else None,
        effective_start=_parse_date(row["effective_start"]),
        effective_end=_parse_date(row["effective_end"]),
        policy_url=str(row["policy_url"]) if row["policy_url"] else None,
        last_verified=_parse_date(row["last_verified"]),
        drug_id=str(row["drug_id"]) if row["drug_id"] else None,
        notes=str(row["notes"]) if row["notes"] else None,
    )


def fetch_admin_fee(
    cpt: str,
    use_qpp: bool = True,
    db_path: Optional[str] = None
) -> Optional[AdminFee]:
    """
    Fetch administration fee from Physician Fee Schedule tables.
    
    Your PFS tables have:
      - non_facility_total (RVUs for office setting)
      - facility_total (RVUs for hospital setting)
      - conv_factor (conversion factor, ~$33.29)
    
    Payment = total_RVU × conversion_factor
    """
    table = "drug_values_QPP_apr2026" if use_qpp else "drug_values_nonQPP_apr2026"
    conn = _get_connection(db_path)
    
    row = conn.execute(
        f"""SELECT hcpcs, description, non_facility_total, facility_total, conv_factor
            FROM {table}
            WHERE hcpcs = ? AND mod = ''
            LIMIT 1""",
        (cpt,)
    ).fetchone()
    
    conn.close()
    
    if not row:
        return None
    
    cf = float(row["conv_factor"] or 33.29)
    non_fac_pmt = round(float(row["non_facility_total"] or 0) * cf, 2)
    fac_pmt = round(float(row["facility_total"] or 0) * cf, 2)
    
    return AdminFee(
        cpt=str(row["hcpcs"]),
        description=str(row["description"] or ""),
        non_facility_payment=non_fac_pmt,
        facility_payment=fac_pmt,
        conversion_factor=cf,
        qpp_applied=use_qpp,
        eff_year="2026",
    )


def fetch_denial_code(denial_string: str, db_path: Optional[str] = None) -> Optional[DenialCode]:
    """
    Fetch denial code from claims_adjustment_codes (CARC) or remittance_remarks (RARC).
    
    Input formats: 'CO-197', '197', 'N210'
    """
    conn = _get_connection(db_path)
    
    # Parse input
    denial_string = denial_string.strip().upper()
    group_code = None
    code = denial_string
    
    if "-" in denial_string:
        parts = denial_string.split("-", 1)
        group_code = parts[0]
        code = parts[1]
    
    # Try CARC first (claims_adjustment_codes has INTEGER codes)
    try:
        code_int = int(code)
        row = conn.execute(
            "SELECT code, description FROM claims_adjustment_codes WHERE code = ?",
            (code_int,)
        ).fetchone()
        if row:
            conn.close()
            return DenialCode(
                code=str(row["code"]),
                code_type="CARC",
                description=str(row["description"] or ""),
                group_code=group_code,
                recommended_action=_get_recommended_action(str(row["code"]), "CARC"),
            )
    except ValueError:
        pass  # Not an integer code; try RARC
    
    # Try RARC (remittance_remarks has TEXT codes)
    row = conn.execute(
        "SELECT code, description FROM remittance_remarks WHERE code = ?",
        (code,)
    ).fetchone()
    
    conn.close()
    
    if row:
        return DenialCode(
            code=str(row["code"]),
            code_type="RARC",
            description=str(row["description"] or ""),
            group_code=group_code,
            recommended_action=_get_recommended_action(str(row["code"]), "RARC"),
        )
    
    return None


def _get_recommended_action(code: str, code_type: str) -> str:
    """Return recommended action for common denial codes."""
    actions = {
        "197": (
            "Submit a retroactive prior authorization request immediately. "
            "Include diagnosis, treatment history, and prescribing physician attestation of medical necessity. "
            "Reference the payer's medical policy. If denied, file a formal appeal within 180 days."
        ),
        "N210": (
            "Submit retroactive prior authorization. This code confirms the denial was for missing preauthorization. "
            "Include the same documentation as for CARC 197."
        ),
        "16": (
            "Correct the billing error and resubmit. Verify HCPCS code, NDC, units billed, "
            "and modifiers match the service provided."
        ),
        "N56": (
            "Resubmit with the correct HCPCS code. Check the payer's preferred product list. "
            "This denial often means you billed for a non-preferred product when a preferred alternative exists."
        ),
        "170": (
            "Verify site of care is consistent with payer policy. Resubmit with correct place of service code "
            "or provide documentation supporting medical necessity for the site used."
        ),
        "31": (
            "Verify patient eligibility and coverage dates. Confirm member ID is correct. "
            "If patient was eligible at time of service, request reprocessing with eligibility documentation."
        ),
        "N395": (
            "Submit additional medical records supporting medical necessity. Include diagnosis, "
            "prior treatment history, clinical notes, and relevant lab results."
        ),
        "N519": (
            "File an appeal with comprehensive medical necessity documentation. "
            "Include clinical evidence, diagnosis codes, and cite the payer's own coverage policy "
            "showing the service meets medical necessity criteria."
        ),
    }
    return actions.get(code, f"Review {code_type} code {code} and payer policy. Contact payer for specific reprocessing instructions.")


# ============================================================================
# Calculation Helpers
# ============================================================================

def calculate_billing_units(total_dose_mg: float, billing_unit_size_mg: int) -> Tuple[int, Optional[float]]:
    """
    Calculate billable units from total dose.
    
    Returns (billing_units, wasted_mg).
    wasted_mg is None if dose divides evenly; otherwise it's the amount eligible for JW modifier.
    
    For MVP: raises on non-even doses if strict mode.
    For production: returns units with waste for JW billing.
    """
    if billing_unit_size_mg <= 0:
        raise ValueError("Billing unit size must be greater than zero.")
    
    exact_units = total_dose_mg / billing_unit_size_mg
    
    if math.isclose(exact_units, round(exact_units), rel_tol=1e-6):
        return int(round(exact_units)), None
    
    # Drug waste: round up to nearest unit
    billable_units = math.ceil(exact_units)
    wasted_mg = (billable_units * billing_unit_size_mg) - total_dose_mg
    
    return billable_units, round(wasted_mg, 1)


def apply_sequester(allowed_amount: float, is_medicare: bool) -> Tuple[float, float, bool]:
    """
    Apply Medicare sequester if applicable.
    
    Returns (net_payment, sequester_amount, was_applied).
    """
    if not is_medicare:
        return allowed_amount, 0.0, False
    
    sequester_amount = allowed_amount * Constants.SEQUESTER_RATE * Constants.MEDICARE_COINSURANCE
    net_payment = allowed_amount - sequester_amount
    return net_payment, round(sequester_amount, 2), True


# ============================================================================
# Main Engine
# ============================================================================

def calculate_verdict(patient: PatientCase, db_path: Optional[str] = None) -> Verdict:
    """
    Calculate coverage verdict and financial breakdown for a buy-and-bill drug.
    
    This is the core engine — Layers A, B, and C combined.
    
    Args:
        patient: PatientCase with payer, product, weight, dosing
        db_path: Path to SQLite database (uses default if None)
    
    Returns:
        Verdict object with coverage status, financials, and risk metrics.
    """
    
    verdict = Verdict(
        hcpcs_queried=patient.hcpcs.upper(),
        is_medicare=patient.is_medicare,
    )
    
    # -----------------------------------------------------------------------
    # Step 1: Fetch drug pricing (Layer A — reference data)
    # -----------------------------------------------------------------------
    drug = fetch_drug_pricing(patient.hcpcs, db_path)
    
    if drug is None:
        verdict.status = "unknown"
        verdict.status_detail = (
            f"HCPCS code '{patient.hcpcs}' not found in payment_limits_072026. "
            "Verify the code and ensure the ASP pricing file has been imported."
        )
        return verdict
    
    verdict.drug_description = drug.drug_name or drug.short_description
    verdict.effective_quarter = drug.effective_quarter
    
    # -----------------------------------------------------------------------
    # Step 2: Calculate dosing and billing units
    # -----------------------------------------------------------------------
    total_dose_mg = patient.total_dose_mg
    verdict.total_dose_mg = round(total_dose_mg, 1)
    
    try:
        billing_units, wasted_mg = calculate_billing_units(total_dose_mg, drug.dosage_mg)
    except ValueError as e:
        verdict.status = "unknown"
        verdict.status_detail = str(e)
        return verdict
    
    verdict.billing_units = billing_units
    
    if wasted_mg and wasted_mg > 0:
        verdict.warnings.append(
            f"Drug waste: {wasted_mg} mg will be discarded. "
            "Bill JW modifier for the wasted portion if required by payer."
        )
    
    # -----------------------------------------------------------------------
    # Step 3: Calculate allowed amount (brief §7)
    # -----------------------------------------------------------------------
    allowed_amount = billing_units * drug.payment_limit
    verdict.allowed_amount = round(allowed_amount, 2)
    
    # -----------------------------------------------------------------------
    # Step 4: Determine acquisition cost (brief §7)
    # -----------------------------------------------------------------------
    if patient.acquisition_cost_override is not None:
        acquisition_cost = patient.acquisition_cost_override
    else:
        acquisition_cost = billing_units * drug.asp_implied
    verdict.acquisition_cost = round(acquisition_cost, 2)
    
    # -----------------------------------------------------------------------
    # Step 5: Calculate gross margin (brief §7)
    # -----------------------------------------------------------------------
    gross_margin = allowed_amount - acquisition_cost
    verdict.gross_margin = round(gross_margin, 2)
    
    if gross_margin < 0:
        verdict.warnings.append(
            f"Gross margin is negative (${gross_margin:.2f}). "
            "Check that payment_limit and uplift_pct are correct for this product. "
            "This product may be priced below acquisition cost."
        )
    
    # -----------------------------------------------------------------------
    # Step 6: Apply Medicare sequester (brief §3, §7)
    # -----------------------------------------------------------------------
    net_drug_payment, sequester_amount, sequester_applied = apply_sequester(
        allowed_amount, patient.is_medicare
    )
    verdict.sequester_applied = sequester_applied
    
    net_margin = net_drug_payment - acquisition_cost
    verdict.net_margin = round(net_margin, 2)
    
    # -----------------------------------------------------------------------
    # Step 7: Fetch admin fee (Layer A — PFS data)
    # -----------------------------------------------------------------------
    admin = fetch_admin_fee(patient.cpt_admin, patient.use_qpp, db_path)
    
    if admin is not None:
        verdict.admin_fee = admin.non_facility_payment
    else:
        verdict.admin_fee = 0.0
        verdict.warnings.append(
            f"Administration fee for CPT {patient.cpt_admin} not found in "
            f"{'QPP' if patient.use_qpp else 'non-QPP'} fee schedule. "
            "Total revenue excludes infusion administration payment."
        )
    
    verdict.total_revenue = round(verdict.net_margin + verdict.admin_fee, 2)
    
    # -----------------------------------------------------------------------
    # Step 8: Calculate denial exposure and breakeven (brief §3, §7)
    # -----------------------------------------------------------------------
    verdict.denial_exposure = round(acquisition_cost, 2)
    
    if verdict.net_margin > 0.01:
        verdict.breakeven_infusions = math.ceil(
            verdict.denial_exposure / verdict.net_margin
        )
    elif abs(verdict.net_margin) <= 0.01:
        verdict.breakeven_infusions = None
        verdict.warnings.append(
            "Net margin is approximately zero. "
            "A denial can never be recovered through margin."
        )
    else:
        verdict.breakeven_infusions = None
        verdict.warnings.append(
            f"Net margin is negative (${verdict.net_margin:.2f}). "
            "Every infusion loses money even without denials."
        )
    
    if verdict.breakeven_infusions is not None and verdict.breakeven_infusions > 100:
        verdict.warnings.append(
            f"Breakeven requires {verdict.breakeven_infusions} clean infusions per denial. "
            "This product carries extreme financial risk."
        )
    
    # -----------------------------------------------------------------------
    # Step 9: Determine coverage status (Layer B — coverage rules)
    # -----------------------------------------------------------------------
    coverage = fetch_coverage_rule(
        patient.payer_id, patient.hcpcs, patient.line_of_business, db_path
    )
    
    if coverage is None:
        verdict.status = "unknown"
        verdict.status_detail = (
            f"No coverage rule found for {patient.payer_id} ({patient.line_of_business}) "
            f"and HCPCS {patient.hcpcs}. Verify coverage manually before administering."
        )
        return verdict
    
    # Check policy effective dates
    today = date.today()
    
    if coverage.effective_start and today < coverage.effective_start:
        verdict.policy_expired = True
        verdict.warnings.append(
            f"Coverage policy effective date is {coverage.effective_start}. "
            "Policy is not yet active."
        )
    
    if coverage.effective_end and today > coverage.effective_end:
        verdict.policy_expired = True
        verdict.warnings.append(
            f"Coverage policy expired on {coverage.effective_end}. "
            "Verify with payer directly."
        )
    
    verdict.policy_url = coverage.policy_url
    verdict.pa_required = coverage.pa_required
    verdict.site_of_care = coverage.site_of_care_pref
    
    # Determine coverage status
    if coverage.preferred and not coverage.pa_required:
        # Best case: preferred product, no PA
        verdict.status = "covered"
        verdict.status_detail = (
            f"HCPCS {patient.hcpcs} ({verdict.drug_description}) is preferred by "
            f"{coverage.payer_id}. No prior authorization required. Proceed with confidence."
        )
    
    elif coverage.preferred and coverage.pa_required:
        # Preferred but PA still required
        verdict.status = "pa_required"
        verdict.status_detail = (
            f"HCPCS {patient.hcpcs} ({verdict.drug_description}) is preferred by "
            f"{coverage.payer_id}, but prior authorization is required. "
            "Obtain PA before administration to avoid denial (CO-197)."
        )
    
    elif not coverage.preferred and coverage.has_preferred_alternative:
        # Wrong product — switch to preferred alternative
        verdict.status = "wrong_product"
        verdict.preferred_hcpcs = coverage.preferred_alt_hcpcs
        verdict.status_detail = (
            f"HCPCS {patient.hcpcs} ({verdict.drug_description}) is NOT preferred by "
            f"{coverage.payer_id}. Switch to {coverage.preferred_alt_hcpcs} "
            "to avoid denial or delayed payment."
        )
        if coverage.pa_required:
            verdict.status_detail += (
                f" If you proceed with {patient.hcpcs}, prior authorization is required "
                "and may require documentation of trial/failure of the preferred product."
            )
        # Add margin comparison if we can fetch the preferred product's pricing
        preferred_drug = fetch_drug_pricing(coverage.preferred_alt_hcpcs, db_path)
        if preferred_drug:
            verdict.warnings.append(
                f"Preferred product {coverage.preferred_alt_hcpcs} payment limit: "
                f"${preferred_drug.payment_limit:.2f}/unit vs. ${drug.payment_limit:.2f}/unit "
                f"for {patient.hcpcs}. Uplift: {preferred_drug.uplift_pct*100:.0f}% vs. "
                f"{drug.uplift_pct*100:.0f}%."
            )
    
    elif not coverage.preferred and not coverage.has_preferred_alternative:
        # Not preferred, but no specific alternative listed
        verdict.status = "pa_required"
        verdict.status_detail = (
            f"HCPCS {patient.hcpcs} ({verdict.drug_description}) is not preferred by "
            f"{coverage.payer_id}. Prior authorization is likely required. "
            "Check the payer policy for covered alternatives."
        )
    
    else:
        # Fallback
        verdict.status = "unknown"
        verdict.status_detail = (
            "Unable to determine coverage status from available data. Verify manually."
        )
    
    # -----------------------------------------------------------------------
    # Step 10: Additional warnings
    # -----------------------------------------------------------------------
    
    # Check for biosimilar uplift advantage
    if not drug.biosimilar and drug.uplift_pct == Constants.ASP_UPLIFT_ORIGINATOR:
        # This is an originator. Check if biosimilars exist that pay better.
        # (Would query for biosimilar alternatives in the same therapeutic class)
        pass  # Future enhancement
    
    # Check for policy staleness
    if coverage and coverage.last_verified:
        days_since_verification = (today - coverage.last_verified).days
        if days_since_verification > 90:
            verdict.warnings.append(
                f"Coverage policy was last verified {days_since_verification} days ago. "
                "Payer policies may have changed. Consider re-verifying."
            )
    
    return verdict


# ============================================================================
# Denial Translator + Appeal Generator (Layer D)
# ============================================================================

def translate_denial(
    denial_string: str,
    verdict: Optional[Verdict] = None,
    db_path: Optional[str] = None,
) -> Appeal:
    """
    Translate a denial code into plain English and generate a draft appeal letter.
    
    Args:
        denial_string: e.g., 'CO-197', '197', 'N210'
        verdict: Optional Verdict for context in appeal letter
        db_path: Database path
    
    Returns:
        Appeal object with explanation and draft letter.
    """
    
    denial = fetch_denial_code(denial_string, db_path)
    
    if denial is None:
        return Appeal(
            denial_code=denial_string.upper(),
            plain_english_cause=f"Denial code '{denial_string}' not found in database.",
            group_code_explanation="",
            recommended_action="Review the remittance advice and payer policy manually.",
            appeal_letter="",
        )
    
    # Group code explanation
    group_explanations = {
        "CO": (
            "Contractual Obligation — This is a provider write-off. "
            "The practice CANNOT bill the patient for this amount. "
            "The full acquisition cost of the drug is lost unless the denial is overturned on appeal."
        ),
        "PR": (
            "Patient Responsibility — The patient CAN be billed for this amount. "
            "However, collecting from patients is often difficult and may affect patient satisfaction."
        ),
        "OA": (
            "Other Adjustment — Neither patient nor provider is liable. "
            "This is typically an informational adjustment, not a denial of payment."
        ),
    }
    
    group_explanation = group_explanations.get(
        denial.group_code or "",
        "Unknown group code. Check the remittance advice for details."
    )
    
    # Generate appeal letter if verdict is available
    appeal_letter = ""
    if verdict is not None:
        appeal_letter = _generate_appeal_letter(denial, verdict)
    
    return Appeal(
        denial_code=denial.full_code,
        plain_english_cause=denial.description,
        group_code_explanation=group_explanation,
        recommended_action=denial.recommended_action,
        appeal_letter=appeal_letter,
    )


def _generate_appeal_letter(denial: DenialCode, verdict: Verdict) -> str:
    """Generate a draft appeal letter from the verdict and denial information."""
    
    # Calculate the financial impact for the appeal
    financial_impact = (
        f"This denial resulted in a write-off of ${verdict.denial_exposure:.2f} "
        f"for the drug acquisition cost alone. "
        f"This is equivalent to the net margin from approximately "
        f"{verdict.breakeven_infusions or 'many'} clean infusions."
    )
    
    return f"""APPEAL OF DENIED CLAIM

[PRACTICE NAME]
[PRACTICE ADDRESS]
[TAX ID / NPI]

Date: [INSERT DATE]

RE: Appeal of Denied Claim
Payer: [PAYER NAME]
Patient: [PATIENT NAME / ID]
Date of Service: [INSERT DATE]
Claim Reference: [CLAIM NUMBER]

Service: {verdict.hcpcs_queried} — {verdict.drug_description}
Administered: {verdict.total_dose_mg} mg ({verdict.billing_units} billable units)
Allowed Amount: ${verdict.allowed_amount:.2f}

Denial Code: {denial.full_code} ({denial.code_type})
Denial Reason: {denial.description}

Dear Appeals Department,

We are writing to formally appeal the denial of the above-referenced claim.

The claim was denied with {denial.code_type} code {denial.code}: "{denial.description}"

Per the payer's published medical coverage policy
({verdict.policy_url or 'see attached policy document'}),
{verdict.hcpcs_queried} ({verdict.drug_description}) is a covered service.

On the date of service, the patient received {verdict.total_dose_mg} mg of
{verdict.drug_description} ({verdict.billing_units} billable units at the
Medicare ASP payment limit, totaling ${verdict.allowed_amount:.2f}).

This service meets the medical necessity criteria outlined in the payer's
coverage policy. The diagnosis, treatment history, and clinical rationale
supporting medical necessity are attached.

{financial_impact}

We respectfully request:
1. Reconsideration of this denial
2. Reprocessing of the claim at the contracted rate
3. Payment of ${verdict.allowed_amount:.2f} plus applicable administration fees

If additional documentation is required, please contact our office immediately.

Sincerely,

[PROVIDER NAME]
[NPI NUMBER]
[CONTACT INFORMATION]
[PHONE]
[EMAIL]

Attachments:
- Medical records supporting medical necessity
- Payer coverage policy reference
- Prior authorization documentation (if applicable)
"""


# ============================================================================
# Utility Functions