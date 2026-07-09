
class Constants:
    ASP_UPLIFT_ORIGINATOR = 0.06       # ASP+6% for originator products
    ASP_UPLIFT_BIOSIMILAR = 0.08       # ASP+8% for qualifying biosimilars (5-year window)
    SEQUESTER_RATE = 0.02              # 2% Medicare sequester
    MEDICARE_COINSURANCE = 0.80        # Medicare pays 80%, patient pays 20%

    # Default administration CPT codes
    CPT_INFUSION_INITIAL = "96413"     # Chemo infusion, up to 1 hour
    CPT_INFUSION_ADDITIONAL = "96415"  # Each additional hour

    # Database path
    DB_PATH = "/home/kay/reimbursements.db"
