"""
app.py

Streamlit interface for the buy-and-bill coverage verifier.
Run: streamlit run src/app.py
"""
import os
from pathlib import Path
from constants import Constants
import streamlit as st
import sqlite3
from calculator_engine import (
    PatientCase,
    calculate_verdict,
    translate_denial,
)
import dotenv

ENV_PATHS = [
    Path(__file__).resolve().parent / ".env",
    Path(__file__).resolve().parent.parent / ".env",
]
for env_path in ENV_PATHS:
    if env_path.exists():
        dotenv.load_dotenv(dotenv_path=env_path)
        break
else:
    dotenv.load_dotenv()


def risk_tooltip(risk_level, breakeven):
    """Return a tooltip string explaining the risk level."""
    if risk_level == "LOW":
        return (
            f"🟢 LOW RISK — Only {breakeven} clean infusion(s) needed to recover from one denial. "
            "This product carries minimal financial risk."
        )
    elif risk_level == "MEDIUM":
        return (
            f"🟡 MEDIUM RISK — Requires {breakeven} clean infusions to recover from one denial. "
            "Verify prior authorization and preferred status before administering."
        )
    elif risk_level == "HIGH":
        if breakeven is not None:
            return (
                f"🔴 HIGH RISK — Requires {breakeven} clean infusions to recover from one denial. "
                "Confirm coverage, obtain PA, and document medical necessity before proceeding."
            )
        else:
            return (
                "🔴 HIGH RISK — Net margin is negative. Every infusion loses money even without a denial."
            )
    else:
        return (
            "⚪ RISK UNKNOWN — Cannot calculate breakeven. Verify all payer policies manually."
        )

# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

# Point to your database file (adjust path as needed)
#DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reimbursements.db")

DB_PATH = Constants.DB_PATH  # Use the path from constants.py


def get_connection():
    """Get a SQLite connection."""
    conn = sqlite3.connect(os.getenv("DB_PATH") or DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn



# Authentication — simple username/password gate
# ---------------------------------------------------------------------------

def check_password():
    """Returns True if the user enters correct credentials."""

    valid_username = "glenndemo"
    valid_password = "glenn123"

    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    # Already authenticated — skip
    if st.session_state["authenticated"]:
        return True

    # Show login form
    st.title("💉 Field Reimbursement Intelligence")
    st.caption("Buy-and-bill coverage verifier — private beta")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign In")

        if submitted:
            if username == valid_username and password == valid_password:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Invalid username or password.")

    return False


# Gate the entire app behind authentication
if not check_password():
    st.stop()




# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Buy-and-Bill Coverage Verifier",
    page_icon="💉",
    layout="wide",
)

st.title("💉 Buy-and-Bill Coverage Verifier")
st.caption("Pre-service coverage brain — know before you infuse")

# ---------------------------------------------------------------------------
# Cached data loaders
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def get_payers():
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT payer_id FROM coverage_policy ORDER BY payer_id"
    ).fetchall()
    conn.close()
    return [str(r[0]) for r in rows]


@st.cache_data(ttl=300)
def get_all_hcpcs():
    """Get all unique HCPCS codes with drug names."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT DISTINCT cp.hcpcs,
                  (SELECT DrugName FROM asp_ndc_hscp_crosswalk_072026
                   WHERE CAST("_2026_CODE" AS TEXT) = cp.hcpcs LIMIT 1) as drug_name
           FROM coverage_policy cp
           ORDER BY cp.hcpcs"""
    ).fetchall()
    conn.close()
    return [(str(r[0]), str(r[1]) if r[1] else None) for r in rows]


def find_drug_by_code_or_name(user_input, all_drugs):
    """
    Find a drug by HCPCS code or drug name.
    Returns the HCPCS code if found, None otherwise.
    """
    user_input = user_input.strip().upper()

    # Try exact HCPCS match first
    for hcpcs, name in all_drugs:
        if user_input == hcpcs.upper():
            return hcpcs

    # Try partial match on drug name
    for hcpcs, name in all_drugs:
        if name and user_input in name.upper():
            return hcpcs

    # Try partial match on HCPCS code
    for hcpcs, name in all_drugs:
        if user_input in hcpcs.upper():
            return hcpcs

    return None


@st.cache_data(ttl=300)
def get_drug_display_name(hcpcs):
    conn = get_connection()
    row = conn.execute(
        """SELECT DrugName FROM asp_ndc_hscp_crosswalk_072026
           WHERE CAST("_2026_CODE" AS TEXT) = ? LIMIT 1""",
        (hcpcs,),
    ).fetchone()
    conn.close()
    return str(row[0]) if row and row[0] else None


@st.cache_data(ttl=300)
def run_product_comparison(payer_id, hcpcs, line_of_business, weight_kg,
                            dosing_mg_per_kg, use_qpp,  acquisition_override):
    """
    Run the calc engine for a specific drug and return results
    for all products under that HCPCS for the given payer.
    """
    conn = get_connection()

    # Use LEFT JOIN to always return the drug even if no coverage policy exists
    rows = conn.execute(
        """SELECT 
            cp.hcpcs, 
            cp.preferred, 
            cp.pa_required, 
            cp.preferred_alt_hcpcs,
            cp.site_of_care_pref, 
            cp.policy_url, 
            cp.notes,
            cw.DrugName as drug_name,
            cw._2026_CODE as code
        FROM asp_ndc_hscp_crosswalk_072026 cw
        LEFT JOIN coverage_policy cp 
            ON CAST(cw._2026_CODE AS TEXT) = cp.hcpcs 
            AND cp.payer_id = ? 
            AND cp.line_of_business = ?
        WHERE CAST(cw._2026_CODE AS TEXT) = ?""",
        (payer_id, line_of_business, hcpcs),
    ).fetchall()

    conn.close()

    results = []
    for row in rows:
        product_hcpcs = str(row["code"])
        drug_name = str(row["drug_name"]) if row["drug_name"] else "—"
        
        # Check if coverage exists - if all policy fields are None, this is a "no policy" case
        has_coverage = row["hcpcs"] is not None
        is_preferred = str(row["preferred"]).strip().lower() == "true" if has_coverage else False
        #pa_required = str(row["pa_required"]).strip().lower() == "true" if has_coverage else None
        preferred_alt = str(row["preferred_alt_hcpcs"]) if has_coverage and row["preferred_alt_hcpcs"] else None
        #policy_url = str(row["policy_url"]) if has_coverage and row["policy_url"] else None
        
        # Create patient case - this will still calculate financials using ASP even without policy
        patient = PatientCase(
            payer_id=payer_id,
            line_of_business=line_of_business,
            hcpcs=product_hcpcs,
            weight_kg=weight_kg,
            dosing_mg_per_kg=dosing_mg_per_kg,
            acquisition_cost_override=acquisition_override,
            use_qpp=use_qpp,
    
        )
        verdict = calculate_verdict(patient, DB_PATH)

        # Override verdict status if no coverage exists
        if not has_coverage:
            verdict.status = "no_coverage_data"
            verdict.status_detail = f"Coverage data for {payer_id} not available. Verify manually."
            verdict.pa_required = None
            verdict.policy_url = None
            
            # Add warning
            if not verdict.warnings:
                verdict.warnings = []
            verdict.warnings.append(f"⚠️ No coverage policy found for {payer_id} - {product_hcpcs}. Check payer website.")

        results.append({
            "hcpcs": product_hcpcs,
            "drug_name": drug_name,
            "preferred": is_preferred,
            "status": verdict.status,
            "status_detail": verdict.status_detail,
            "preferred_alt": preferred_alt,
            "allowed_amount": verdict.allowed_amount,
            "acquisition_cost": verdict.acquisition_cost,
            "net_margin": verdict.net_margin,
            "admin_fee": verdict.admin_fee,
            "total_revenue": verdict.total_revenue,
            "denial_exposure": verdict.denial_exposure,
            "breakeven_infusions": verdict.breakeven_infusions,
            "risk_level": verdict.risk_level,
            "pa_required": verdict.pa_required,
            "billing_units": verdict.billing_units,
            "total_dose_mg": verdict.total_dose_mg,
            "policy_url": verdict.policy_url,
            "warnings": verdict.warnings,
            "sequester_applied": verdict.sequester_applied,
            "has_coverage": has_coverage,  # New flag for UI
        })

    return results

# ---------------------------------------------------------------------------
# Sidebar — Inputs
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("🔍 Payer & Drug")

    # Payer
    payers = get_payers()
    if not payers:
        st.error("No coverage policies found. Load coverage_policy table first.")
        st.stop()

    payer_id = st.selectbox("Payer", payers)

    # Drug input
    all_drugs = get_all_hcpcs()

    st.subheader("Drug")
    drug_input = st.text_input(
        "Enter HCPCS code or drug name",
        value="J1745",
        help="Examples: 'J1745', 'Remicade', 'infliximab', 'Q5103'",
    )

    # Line of business
    line_of_business = st.selectbox(
        "Line of Business",
        ["commercial", "medicare_advantage", "medicare"],
    )

    st.divider()
    st.header("⚖️ Patient & Dosing")

    weight_kg = st.number_input(
        "Patient Weight (kg)",
        min_value=10.0,
        max_value=200.0,
        value=80.0,
        step=0.1,
    )

    dosing_mg_per_kg = st.number_input(
        "Dosing (mg/kg)",
        min_value=0.1,
        max_value=50.0,
        value=5.0,
        step=0.1,
        help="Infliximab: 5 mg/kg. Trastuzumab: 4-8 mg/kg. Check prescribing info.",
    )

    total_dose = weight_kg * dosing_mg_per_kg
    st.caption(f"Total dose: **{total_dose:.0f} mg**")

    st.divider()
    st.header("⚙️ Options")

    use_qpp = st.checkbox("Use QPP-adjusted admin fees", value=True)

    acquisition_str = st.text_input(
        "Acquisition cost override ($)",
        value="",
        help="Leave blank to use ASP-implied cost.",
    )
    acquisition_override = float(acquisition_str) if acquisition_str.strip() else None


## ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------

tab1, tab2 = st.tabs(["📊 Product Comparison", "📝 Appeal Generator"])

with tab1:
    # ---- Drug lookup ----
    if drug_input.strip():
        matched_hcpcs = find_drug_by_code_or_name(drug_input, all_drugs)

        if matched_hcpcs is None:
            st.warning(f"No drug found matching '{drug_input}'. Try a HCPCS code like 'J1745' or a drug name like 'Remicade'.")
        else:
            drug_name = get_drug_display_name(matched_hcpcs) or "—"
            st.subheader(f"🔍 {matched_hcpcs} — {drug_name}")
            st.caption(f"Payer: **{payer_id}** | {line_of_business}")

            # Run comparison
            if st.button("🔍 Check Coverage", type="primary", use_container_width=True):
                with st.spinner("Calculating coverage and reimbursement..."):
                    results = run_product_comparison(
                        payer_id, matched_hcpcs, line_of_business,
                        weight_kg, dosing_mg_per_kg,
                        use_qpp, acquisition_override,
                    )

                st.session_state["comparison_results"] = results

                if not results:
                    st.warning(f"No coverage rules found for {payer_id} — {matched_hcpcs}.")
                else:
                    # ---- Summary Cards ----
                    preferred_count = sum(1 for r in results if r["preferred"])
                    wrong_count = sum(1 for r in results if r["status"] == "wrong_product")
                    pa_count = sum(1 for r in results if r["pa_required"])

                    card1, card2, card3 = st.columns(3)
                    card1.metric("Products Found", len(results))
                    card2.metric("Preferred Products", preferred_count)
                    card3.metric("Wrong Product Flags", wrong_count, delta_color="inverse")

                    st.divider()

                    # ---- Product Cards ----
                    for r in results:
                        status_icons = {
                            "covered": "✅",
                            "pa_required": "⚠️",
                            "wrong_product": "🚫",
                            "unknown": "❓",
                        }
                        icon = status_icons.get(r["status"], "•")

                        badge = "⭐ PREFERRED" if r["preferred"] else ""
                        pa_badge = "🔒 PA Required" if r["pa_required"] else ""

                        expander_label = (
                            f"{icon} **{r['hcpcs']} — {r['drug_name']}**  "
                            f"{badge}  {pa_badge}  |  "
                            f"Net: ${r['net_margin']:.2f}  |  "
                            f"Risk: {r['risk_level']}"
                        )

                        with st.expander(expander_label, expanded=(r["status"] == "wrong_product")):
                            # ---- Status message ----
                            if r["status"] == "wrong_product":
                                st.error(f"🚫 {r['status_detail']}")

                                # ---- Show the preferred alternative prominently ----
                                if r["preferred_alt"]:
                                    alt_name = get_drug_display_name(r["preferred_alt"])
                                    alt_data = next(
                                        (item for item in results if item["hcpcs"] == r["preferred_alt"]),
                                        None,
                                    )

                                    with st.container(border=True):
                                        st.markdown(f"### 💡 Use **{r['preferred_alt']} — {alt_name}** instead")

                                        if alt_data:
                                            reasons = []
                                            if alt_data["preferred"]:
                                                reasons.append("✅ Preferred by this payer — lower denial risk")
                                            if alt_data["pa_required"] is False:
                                                reasons.append("🔓 No prior authorization required")
                                            elif r["pa_required"] and alt_data["pa_required"]:
                                                reasons.append("🔒 PA still required, but preferred status helps approval")

                                            margin_diff = alt_data["net_margin"] - r["net_margin"]
                                            if margin_diff > 0:
                                                reasons.append(f"💰 ${margin_diff:.2f} more net margin per infusion")
                                            elif margin_diff < 0:
                                                reasons.append(f"💵 Margin is ${abs(margin_diff):.2f} less, but preferred status protects against denials")

                                            risk_order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "UNKNOWN": 3}
                                            if risk_order.get(alt_data["risk_level"], 9) < risk_order.get(r["risk_level"], 9):
                                                reasons.append(f"🛡️ Lower risk ({alt_data['risk_level']} vs {r['risk_level']})")

                                            for reason in reasons:
                                                st.write(reason)

                                            st.divider()
                                            st.caption("**Side-by-side comparison:**")

                                            comp_col1, comp_col2, comp_col3 = st.columns(3)
                                            with comp_col1:
                                                st.metric(
                                                    label="Net Margin",
                                                    value=f"${r['net_margin']:.2f}",
                                                    delta=f"${alt_data['net_margin']:.2f}",
                                                    delta_color="normal",
                                                )
                                            with comp_col2:
                                                risk_delta = None
                                                if r["risk_level"] != alt_data["risk_level"]:
                                                    risk_delta = alt_data["risk_level"]
                                                st.metric(
                                                    label="Risk Level",
                                                    value=r["risk_level"],
                                                    delta=risk_delta,
                                                    delta_color="inverse" if risk_order.get(alt_data["risk_level"], 9) < risk_order.get(r["risk_level"], 9) else "normal",
                                                    help=risk_tooltip(r["risk_level"], r.get("breakeven_infusions")),
                                                )
                                            with comp_col3:
                                                st.metric(
                                                    label="Preferred",
                                                    value="No" if not r["preferred"] else "Yes",
                                                    delta="Yes" if alt_data["preferred"] else "No",
                                                    delta_color="normal",
                                                )
                                        else:
                                            st.write(f"Switch to **{r['preferred_alt']} — {alt_name}** for preferred coverage under this payer.")

                            elif not r["preferred"] and not r["preferred_alt"]:
                                # Product is not preferred, but no specific alternative exists
                                # This is the "equal coverage" case (e.g., Aetna bevacizumab)
                                # OR the "excluded, try all preferred" case (e.g., UHC IVIG non-preferred)

                                # Check if there ARE preferred products in the results
                                preferred_in_results = [item for item in results if item["preferred"]]

                                if preferred_in_results:
                                    # There are preferred alternatives — list them
                                    with st.container(border=True):
                                        st.markdown("### 💡 Preferred alternatives available")
                                        st.write("This product is not preferred. Consider switching to:")
                                        for pref in preferred_in_results:
                                            pref_name = get_drug_display_name(pref["hcpcs"]) or "—"
                                            st.write(f"• **{pref['hcpcs']} — {pref_name}** (Net: ${pref['net_margin']:.2f}, Risk: {pref['risk_level']})")
                                else:
                                    # No preferred products at all — everything is equal
                                    st.info(
                                        "ℹ️ All products in this class are treated equally under this payer's policy. "
                                        "No specific preferred alternative exists. Prior authorization is required for all products."
                                    )

                            elif r["status"] == "pa_required":
                                if r["preferred"]:
                                    st.warning(f"⚠️ {r['status_detail']}")
                                else:
                                    # Not preferred, but status is still PA_REQUIRED (not WRONG_PRODUCT)
                                    st.warning(f"⚠️ {r['status_detail']}")

                                    # Check if there are preferred alternatives
                                    preferred_in_results = [item for item in results if item["preferred"]]
                                    if preferred_in_results:
                                        with st.container(border=True):
                                            st.markdown("### 💡 Preferred alternatives available")
                                            st.write("While this product may be covered with PA, preferred options exist:")
                                            for pref in preferred_in_results:
                                                pref_name = get_drug_display_name(pref["hcpcs"]) or "—"
                                                st.write(f"• **{pref['hcpcs']} — {pref_name}** (Net: ${pref['net_margin']:.2f}, Risk: {pref['risk_level']})")

                            elif r["status"] == "covered":
                                st.success(f"✅ {r['status_detail']}")
                            else:
                                st.info(f"❓ {r['status_detail']}")

                            # Dose info
                            st.caption(
                                f"Dose: {r['total_dose_mg']} mg | "
                                f"Billing units: {r['billing_units']} | "
                                f"{'🔒 Sequester applied' if r['sequester_applied'] else ''}"
                            )

                            # Financial breakdown
                            fin_col1, fin_col2, fin_col3, fin_col4, fin_col5 = st.columns(5)
                            fin_col1.metric("Allowed", f"${r['allowed_amount']:,.2f}")
                            fin_col2.metric("Acquisition", f"${r['acquisition_cost']:,.2f}")
                            fin_col3.metric("Net Margin", f"${r['net_margin']:,.2f}")
                            fin_col4.metric("Admin Fee", f"${r['admin_fee']:,.2f}")
                            fin_col5.metric("Total Revenue", f"${r['total_revenue']:,.2f}")

                            # Risk
                            risk_col1, risk_col2, risk_col3 = st.columns(3)
                            risk_col1.metric(
                                "Risk Level",
                                r["risk_level"],
                                help=risk_tooltip(r["risk_level"], r.get("breakeven_infusions")),
                            )
                            risk_col2.metric("Denial Exposure", f"${r['denial_exposure']:,.2f}")
                            if r["breakeven_infusions"] is not None:
                                risk_col3.metric("Breakeven", r["breakeven_infusions"])
                            else:
                                risk_col3.metric("Breakeven", "N/A")

                            # Warnings
                            if r["warnings"]:
                                for w in r["warnings"]:
                                    st.caption(f"⚠️ {w}")

                            # Policy link
                            if r["policy_url"]:
                                st.caption(f"📋 [View payer policy]({r['policy_url']})")

                            # Select for appeal
                            if st.button(
                                f"📋 Select {r['hcpcs']} for appeal",
                                key=f"select_{r['hcpcs']}",
                            ):
                                patient = PatientCase(
                                    payer_id=payer_id,
                                    line_of_business=line_of_business,
                                    hcpcs=r["hcpcs"],
                                    weight_kg=weight_kg,
                                    dosing_mg_per_kg=dosing_mg_per_kg,
                                    acquisition_cost_override=acquisition_override,
                                    use_qpp=use_qpp,
                                )
                                st.session_state["last_verdict"] = calculate_verdict(patient, DB_PATH)
                                st.success(f"✅ {r['hcpcs']} selected. Go to Appeal Generator tab.")
                                st.rerun()

# ---- Tab 2: Appeal Generator ----
with tab2:
    st.subheader("📝 Generate an Appeal Letter")

    verdict = st.session_state.get("last_verdict")

    if verdict:
        st.info(
            f"**Selected product:** {verdict.hcpcs_queried} — {verdict.drug_description}  \n"
            f"Status: {verdict.status.upper()}  |  "
            f"Net margin: ${verdict.net_margin:.2f}  |  "
            f"Denial exposure: ${verdict.denial_exposure:.2f}"
        )
    else:
        st.caption("Select a product from the Product Comparison tab first.")

    denial_input = st.text_input(
        "Enter denial code from remittance advice",
        value="CO-197",
        help="Format: 'CO-197', '197', or 'N210'. Find this on your EOB/ERA.",
    )

    if st.button("📄 Generate Appeal", use_container_width=True):
        with st.spinner("Generating appeal..."):
            appeal = translate_denial(denial_input, verdict, DB_PATH)

        st.subheader("📋 Denial Explanation")
        st.write(f"**Code:** `{appeal.denial_code}`")
        st.write(f"**Meaning:** {appeal.plain_english_cause}")

        if appeal.group_code_explanation:
            st.write(f"**Financial Impact:** {appeal.group_code_explanation}")

        st.write(f"**Recommended Action:** {appeal.recommended_action}")

        if appeal.appeal_letter:
            st.divider()
            st.subheader("✉️ Draft Appeal Letter")
            st.text_area(
                "Copy and customize this letter",
                value=appeal.appeal_letter,
                height=400,
                key="appeal_text",
            )
            st.download_button(
                "📥 Download as .txt",
                data=appeal.appeal_letter,
                file_name=f"appeal_{denial_input.replace('-', '_')}.txt",
                mime="text/plain",
                use_container_width=True,
            )
        else:
            st.info("Select a product first for a personalized letter with financial details.")

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.divider()
st.caption(
    "Field Reimbursement Intelligence — Prototype v0. "
    "Coverage rules are hand-curated. Verify against current payer policies before clinical use."
)