"""
cli.py

Command-line interface for the buy-and-bill coverage verifier.
Run: python cli.py
"""

import sys
import sqlite3
from calculator_engine import calculate_verdict, translate_denial, PatientCase
from constants import Constants



def print_divider(char="=", length=60):
    print(char * length)


def list_payers():
    """Show all available payers."""
    conn = sqlite3.connect(Constants.DB_PATH)
    rows = conn.execute(
        "SELECT DISTINCT payer_id FROM coverage_policy ORDER BY payer_id"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def list_products(payer_id: str):
    """Show products for a given payer."""
    conn = sqlite3.connect(Constants.DB_PATH)
    rows = conn.execute(
        """SELECT DISTINCT cp.hcpcs,
                  (SELECT DrugName FROM asp_ndc_hscp_crosswalk_072026
                   WHERE CAST("_2026_CODE" AS TEXT) = cp.hcpcs LIMIT 1) as drug_name
           FROM coverage_policy cp
           WHERE cp.payer_id = ?
           ORDER BY cp.hcpcs""",
        (payer_id,),
    ).fetchall()
    conn.close()
    return rows


def display_verdict(verdict):
    """Pretty-print a verdict."""
    print_divider()
    print("COVERAGE VERDICT")
    print_divider()

    # Status
    status_icons = {
        "covered": "✅",
        "pa_required": "⚠️",
        "wrong_product": "🚫",
        "unknown": "❓",
    }
    icon = status_icons.get(verdict.status, "•")
    print(f"\n{icon}  Status: {verdict.status.upper()}")
    print(f"   {verdict.status_detail}")

    if verdict.preferred_hcpcs:
        print(f"\n💡  SWITCH TO: {verdict.preferred_hcpcs}")

    # Warnings
    if verdict.warnings:
        print(f"\n⚠️  Warnings:")
        for w in verdict.warnings:
            print(f"   • {w}")

    # Financials
    print_divider("-")
    print("FINANCIAL BREAKDOWN")
    print_divider("-")
    print(f"   Total Dose:            {verdict.total_dose_mg} mg")
    print(f"   Billing Units:          {verdict.billing_units}")
    print(f"   Allowed Amount:         ${verdict.allowed_amount:>10,.2f}")
    print(f"   Acquisition Cost:       ${verdict.acquisition_cost:>10,.2f}")
    print(f"   Gross Margin:           ${verdict.gross_margin:>10,.2f}")
    if verdict.sequester_applied:
        print(f"   Net Margin (sequester): ${verdict.net_margin:>10,.2f}")
    else:
        print(f"   Net Margin:             ${verdict.net_margin:>10,.2f}")
    print(f"   Admin Fee (CPT 96413):  ${verdict.admin_fee:>10,.2f}")
    print(f"   ───────────────────────────────")
    print(f"   Total Revenue:          ${verdict.total_revenue:>10,.2f}")

    # Risk
    print_divider("-")
    print("RISK ASSESSMENT")
    print_divider("-")
    print(f"   Risk Level:             {verdict.risk_level}")
    print(f"   Denial Exposure:        ${verdict.denial_exposure:>10,.2f}")
    if verdict.breakeven_infusions is not None:
        print(f"   Breakeven Infusions:    {verdict.breakeven_infusions}")
    else:
        print(f"   Breakeven Infusions:    N/A (margin is zero or negative)")

    if verdict.policy_url:
        print(f"\n📋  Policy: {verdict.policy_url}")


def display_appeal(appeal):
    """Pretty-print an appeal."""
    print_divider()
    print("APPEAL GENERATOR")
    print_divider()
    print(f"\n   Code:       {appeal.denial_code}")
    print(f"   Meaning:    {appeal.plain_english_cause}")
    print(f"   Impact:     {appeal.group_code_explanation}")
    print(f"   Action:     {appeal.recommended_action}")

    if appeal.appeal_letter:
        print_divider("-")
        print("DRAFT APPEAL LETTER")
        print_divider("-")
        print(appeal.appeal_letter)
        print_divider("-")
        print("(Copy the letter above and customize bracketed fields)")


def main():
    print_divider()
    print("  BUY-AND-BILL COVERAGE VERIFIER")
    print("  Field Reimbursement Intelligence — CLI")
    print_divider()

    # ----- Step 1: Select Payer -----
    payers = list_payers()
    if not payers:
        print("\n❌ No payers found in coverage_policy table.")
        print("   Load your coverage policies first.")
        sys.exit(1)

    print("\nAvailable Payers:")
    for i, p in enumerate(payers, 1):
        print(f"  {i}. {p}")

    while True:
        try:
            choice = int(input(f"\nSelect payer (1-{len(payers)}): "))
            if 1 <= choice <= len(payers):
                payer_id = payers[choice - 1]
                break
        except ValueError:
            pass
        print(f"  Enter a number between 1 and {len(payers)}.")

    # ----- Step 2: Select Product -----
    products = list_products(payer_id)
    if not products:
        print(f"\n❌ No products found for payer '{payer_id}'.")
        sys.exit(1)

    print(f"\nProducts for {payer_id}:")
    for i, (hcpcs, name) in enumerate(products, 1):
        display = f"{hcpcs} — {name or 'Unknown'}"
        print(f"  {i}. {display}")

    while True:
        try:
            choice = int(input(f"\nSelect product (1-{len(products)}): "))
            if 1 <= choice <= len(products):
                hcpcs = products[choice - 1][0]
                product_name = products[choice - 1][1] or "Unknown"
                break
        except ValueError:
            pass
        print(f"  Enter a number between 1 and {len(products)}.")

    # ----- Step 3: Line of Business -----
    print("\nLine of Business:")
    print("  1. commercial")
    print("  2. medicare_advantage")
    print("  3. medicare")

    lob_map = {"1": "commercial", "2": "medicare_advantage", "3": "medicare"}
    while True:
        choice = input("Select (1-3) [1]: ").strip() or "1"
        if choice in lob_map:
            line_of_business = lob_map[choice]
            break
        print("  Enter 1, 2, or 3.")

    # ----- Step 4: Patient Details -----
    print(f"\nPatient & Dosing")
    while True:
        try:
            weight = input("Patient weight (kg) [80]: ").strip()
            weight_kg = float(weight) if weight else 80.0
            if 10 <= weight_kg <= 200:
                break
        except ValueError:
            pass
        print("  Enter a weight between 10 and 200 kg.")

    while True:
        try:
            dose = input(f"Dosing (mg/kg) [5.0]: ").strip()
            dosing_mg_per_kg = float(dose) if dose else 5.0
            if 0.1 <= dosing_mg_per_kg <= 50:
                break
        except ValueError:
            pass
        print("  Enter a dose between 0.1 and 50 mg/kg.")

    # ----- Step 5: Options -----
    print(f"\nOptions")
    acq_input = input("Acquisition cost override ($) [ASP-implied]: ").strip()
    acquisition_override = float(acq_input) if acq_input else None

    qpp_input = input("Use QPP-adjusted admin fees? (y/n) [y]: ").strip().lower()
    use_qpp = qpp_input != "n"

    # ----- Step 6: Run Engine -----
    print(f"\n🔍 Checking coverage for {payer_id} — {hcpcs} ({product_name})...")

    patient = PatientCase(
        payer_id=payer_id,
        line_of_business=line_of_business,
        hcpcs=hcpcs,
        weight_kg=weight_kg,
        dosing_mg_per_kg=dosing_mg_per_kg,
        acquisition_cost_override=acquisition_override,
        use_qpp=use_qpp,
    )

    verdict = calculate_verdict(patient, Constants.DB_PATH)
    display_verdict(verdict)

    # ----- Step 7: Appeal Generator -----
    print_divider()
    appeal_choice = input("\nGenerate an appeal? Enter denial code (e.g., CO-197) or press Enter to skip: ").strip()

    if appeal_choice:
        appeal = translate_denial(appeal_choice, verdict, Constants.DB_PATH)
        display_appeal(appeal)

    print_divider()
    print("Done. Run again: python3 cli.py")
    print_divider()


if __name__ == "__main__":
    main()