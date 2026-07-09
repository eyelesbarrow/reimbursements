"""
models.py

Unified data models for the buy-and-bill reimbursement engine.
All SQLAlchemy ORM models use consistent naming conventions.
Domain dataclasses provide type-safe objects for the calc engine.
"""

from sqlalchemy import Column, Float, Integer, String, Date, Text, Boolean
from sqlalchemy.orm import declarative_base, relationship
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import date

Base = declarative_base()


# ============================================================================
# SQLAlchemy ORM Models (Database Tables)
# ============================================================================

class PaymentLimit(Base):
    """
    CMS ASP Drug Pricing File — payment limits per HCPCS code.
    Source: payment_limits_072026
    """
    __tablename__ = "payment_limits_072026"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hcpcs_code = Column("HCPCSCode", String(10), nullable=False, index=True)
    short_description = Column("ShortDescription", String(255))
    hcpcs_dosage = Column("HCPCSCodeDosage", String(50))
    payment_limit = Column("PaymentLimit", Float, nullable=False)
    co_insurance_pct = Column("Co-insurancePercentage", Float)
    vaccine_awp_pct = Column("VaccineAWP%", String(50))
    vaccine_limit = Column("VaccineLimit", String(50))
    blood_awp_pct = Column("BloodAWP%", String(50))
    blood_limit = Column("Bloodlimit", String(50))
    clotting_factor = Column("ClottingFactor", String(50))
    notes = Column("Notes", Text)


class ASPNDCCrosswalk(Base):
    """
    ASP NDC-HCPCS Crosswalk — maps NDCs to HCPCS codes.
    Source: asp_ndc_hscp_crosswalk_072026
    """
    __tablename__ = "asp_ndc_hscp_crosswalk_072026"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hcpcs_code = Column("_2026_CODE", String(10), nullable=False, index=True)
    short_description = Column("ShortDescription", String(255))
    labeler_name = Column("LabelerName", String(255))
    ndc = Column("NDC", String(50), nullable=False, index=True)
    drug_name = Column("DrugName", String(255))
    hcpcs_dosage = Column("HCPCSdosage", String(50))
    pkg_size = Column("PkgSize", Float)
    pkg_qty = Column("PkgQty", Integer)
    bill_units = Column("BillUnits", Integer)
    bill_units_per_pkg = Column("BillUnitsPkg", Integer)


class PhysicianFeeSchedule(Base):
    """
    Abstract base for Physician Fee Schedule tables.
    Contains shared columns for both QPP and non-QPP variants.
    """
    __abstract__ = True

    id = Column(Integer, primary_key=True, autoincrement=True)
    hcpcs = Column("hcpcs", String(20), nullable=False, index=True)
    modifier = Column("mod", String(10), default="")
    description = Column("description", String(255))
    status_code = Column("status_code", String(10))
    not_used_for_medicare = Column("not_used_for_medicare", String(10))
    work_rvu = Column("work_rvu", Float)
    non_fac_pe_rvu = Column("non_fac_pe_rvu", Float)
    non_fac_na_indicator = Column("non_fac_na_indicator", String(10))
    facility_pe_rvu = Column("facility_pe_rvu", Float)
    facility_na_indicator = Column("facility_na_indicator", String(10))
    mp_rvu = Column("mp_rvu", Float)
    non_facility_total = Column("non_facility_total", Float)
    facility_total = Column("facility_total", Float)
    pctc_ind = Column("pctc_ind", Integer)
    glob_days = Column("glob_days", String(10))
    pre_op = Column("pre_op", Float)
    intra_op = Column("intra_op", Float)
    post_op = Column("post_op", Float)
    mult_op = Column("mult_op", Integer)
    bilat_surg = Column("bilat_surg", Integer)
    asst_surg = Column("asst_surg", Integer)
    co_surg = Column("co_surg", Integer)
    team_surg = Column("team_surg", Integer)
    pric_ind = Column("pric_ind", Integer)
    endo_base = Column("endo_base", String(10))
    conv_factor = Column("conv_factor", Float)
    physician_supervision = Column("physician_supervision_of_diagnostic_procedure", Integer)
    calc_flag = Column("calc_flag", Integer)
    diagnostic_imaging_family = Column("diagnostic_imaging_family_indicator", Integer)
    non_fac_pe_opps = Column("non_facility_pe_used_for_opps_payment", Float)
    fac_pe_opps = Column("facility_pe_used_for_opps_payment", Float)
    mp_opps = Column("mp_used_for_opps_payment", Float)
    qpp = Column("qpp", Integer)


class PhysicianFeeScheduleQPP(PhysicianFeeSchedule):
    """QPP-adjusted Physician Fee Schedule — April 2026."""
    __tablename__ = "drug_values_QPP_apr2026"


class PhysicianFeeScheduleNonQPP(PhysicianFeeSchedule):
    """Non-QPP Physician Fee Schedule — April 2026."""
    __tablename__ = "drug_values_nonQPP_apr2026"


class CoveragePolicy(Base):
    """
    Payer coverage policies for buy-and-bill drugs.
    Source: coverage_policy
    """
    __tablename__ = "coverage_policy"

    id = Column(Integer, primary_key=True, autoincrement=True)
    drug_id = Column("drug_id", String(50))
    payer_id = Column("payer_id", String(50), nullable=False, index=True)
    line_of_business = Column("line_of_business", String(50), nullable=False)
    hcpcs = Column("hcpcs", String(10), nullable=False, index=True)
    pa_required = Column("pa_required", String(10), default="False")
    preferred = Column("preferred", String(10), default="False")
    preferred_alt_hcpcs = Column("preferred_alt_hcpcs", String(10))
    site_of_care_pref = Column("site_of_care_pref", String(50))
    effective_start = Column("effective_start", Date)
    effective_end = Column("effective_end", Date)
    policy_url = Column("policy_url", String(500))
    last_verified = Column("last_verified", Date)
    notes = Column("notes", Text)


class ClaimsAdjustmentCode(Base):
    """
    Claim Adjustment Reason Codes (CARC).
    Source: claims_adjustment_codes
    """
    __tablename__ = "claims_adjustment_codes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column("code", String(10), nullable=False, unique=True)
    description = Column("description", Text)


class RemittanceRemark(Base):
    """
    Remittance Advice Remark Codes (RARC).
    Source: remittance_remarks
    """
    __tablename__ = "remittance_remarks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column("code", String(10), nullable=False, unique=True)
    description = Column("description", Text)


# ============================================================================
# Domain Dataclasses (Type-safe objects for the calc engine)
# ============================================================================

@dataclass
class DrugPricing:
    """
    Consolidated drug pricing from payment_limits + crosswalk.
    """
    hcpcs: str
    short_description: str
    dosage_mg: int
    payment_limit: float
    uplift_pct: float = 0.06
    asp_implied: float = 0.0
    effective_quarter: str = "2026Q3"
    biosimilar: bool = False
    labeler_name: Optional[str] = None
    drug_name: Optional[str] = None
    ndcs: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.asp_implied == 0.0 and self.payment_limit > 0:
            self.asp_implied = round(self.payment_limit / (1 + self.uplift_pct), 4)


@dataclass
class CoverageRule:
    """
    Payer coverage policy for a specific drug product.
    """
    payer_id: str
    line_of_business: str
    hcpcs: str
    pa_required: bool = False
    preferred: bool = False
    preferred_alt_hcpcs: Optional[str] = None
    site_of_care_pref: Optional[str] = None
    effective_start: Optional[date] = None
    effective_end: Optional[date] = None
    policy_url: Optional[str] = None
    last_verified: Optional[date] = None
    drug_id: Optional[str] = None
    notes: Optional[str] = None

    @property
    def is_active(self) -> bool:
        today = date.today()
        if self.effective_start and today < self.effective_start:
            return False
        if self.effective_end and today > self.effective_end:
            return False
        return True

    @property
    def has_preferred_alternative(self) -> bool:
        if self.preferred:
            return False
        if not self.preferred_alt_hcpcs:
            return False
        return self.preferred_alt_hcpcs.strip().upper() not in ("NULL", "NONE", "")


@dataclass
class AdminFee:
    """
    Physician Fee Schedule payment for drug administration.
    """
    cpt: str
    description: str
    non_facility_payment: float
    facility_payment: float
    conversion_factor: float
    qpp_applied: bool = True
    eff_year: str = "2026"

    @classmethod
    def from_pfs_row(cls, row, use_qpp: bool = True) -> "AdminFee":
        cf = float(getattr(row, "conv_factor", 33.29))
        non_fac_total = float(getattr(row, "non_facility_total", 0))
        fac_total = float(getattr(row, "facility_total", 0))

        return cls(
            cpt=str(getattr(row, "hcpcs", "")),
            description=str(getattr(row, "description", "")),
            non_facility_payment=round(non_fac_total * cf, 2),
            facility_payment=round(fac_total * cf, 2),
            conversion_factor=cf,
            qpp_applied=use_qpp,
        )


@dataclass
class DenialCode:
    """
    Claim adjustment or remittance remark code with explanation.
    """
    code: str
    code_type: str  # 'CARC' or 'RARC'
    description: str = ""
    group_code: Optional[str] = None
    recommended_action: str = ""

    @property
    def is_contractual_writeoff(self) -> bool:
        return self.group_code == "CO"

    @property
    def full_code(self) -> str:
        if self.group_code:
            return f"{self.group_code}-{self.code}"
        return self.code


@dataclass
class PatientCase:
    """
    Clinical and administrative inputs for a coverage check.
    """
    payer_id: str
    line_of_business: str
    hcpcs: str
    weight_kg: float
    dosing_mg_per_kg: float = 5.0
    acquisition_cost_override: Optional[float] = None
    cpt_admin: str = "96413"
    use_qpp: bool = True

    @property
    def total_dose_mg(self) -> float:
        return self.weight_kg * self.dosing_mg_per_kg

    @property
    def is_medicare(self) -> bool:
        lob = self.line_of_business.lower()
        pid = self.payer_id.lower()
        return "medicare" in lob or "medicare" in pid


@dataclass
class Verdict:
    """
    Complete coverage verdict and financial breakdown.
    """
    status: str = ""
    status_detail: str = ""
    warnings: List[str] = field(default_factory=list)

    hcpcs_queried: str = ""
    drug_description: str = ""
    preferred_hcpcs: Optional[str] = None

    pa_required: Optional[bool] = None
    policy_url: Optional[str] = None
    policy_expired: bool = False
    site_of_care: Optional[str] = None

    total_dose_mg: float = 0.0
    billing_units: int = 0

    allowed_amount: float = 0.0
    acquisition_cost: float = 0.0
    gross_margin: float = 0.0
    net_margin: float = 0.0
    admin_fee: float = 0.0
    total_revenue: float = 0.0
    sequester_applied: bool = False

    denial_exposure: float = 0.0
    breakeven_infusions: Optional[int] = None

    effective_quarter: str = ""
    is_medicare: bool = False

    @property
    def summary(self) -> str:
        if self.status == "covered":
            return f"✓ Covered — Net margin ${self.net_margin:.2f}"
        elif self.status == "pa_required":
            return f"⚠ PA Required — Net margin ${self.net_margin:.2f} if approved"
        elif self.status == "wrong_product":
            return f"✗ Wrong product — Switch to {self.preferred_hcpcs}"
        elif self.status == "unknown":
            return "? Coverage unknown — Verify manually"
        return "Status unclear"

    @property
    def risk_level(self) -> str:
        if self.breakeven_infusions is None:
            return "HIGH" if self.net_margin <= 0 else "UNKNOWN"
        if self.breakeven_infusions <= 10:
            return "LOW"
        elif self.breakeven_infusions <= 25:
            return "MEDIUM"
        return "HIGH"


@dataclass
class Appeal:
    """
    Generated appeal package for a denied claim.
    """
    denial_code: str
    plain_english_cause: str
    group_code_explanation: str
    recommended_action: str
    appeal_letter: str = ""

    @property
    def can_bill_patient(self) -> bool:
        return "PR" in self.denial_code.upper()