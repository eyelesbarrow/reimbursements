import unittest

from calculator_engine import (
    calculate_units,
    calculate_pricing_metrics,
    build_pricing_result_from_rows,
)


class CalculatorEngineTests(unittest.TestCase):
    def test_calculate_units_uses_dose_and_billing_unit_size(self) -> None:
        self.assertEqual(calculate_units(400.0, 10.0), 40.0)

    def test_pricing_metrics_use_implied_asp_when_acquisition_is_not_provided(self) -> None:
        metrics = calculate_pricing_metrics(
            total_dose_mg=400.0,
            billing_unit_size_mg=10.0,
            payment_limit=31.041,
            uplift_pct=0.06,
            is_medicare=True,
        )

        self.assertAlmostEqual(metrics.units, 40.0)
        self.assertAlmostEqual(metrics.allowed, 1241.64)
        self.assertAlmostEqual(metrics.asp_implied, 29.283962264150944)
        self.assertAlmostEqual(metrics.acquisition, metrics.asp_implied * metrics.units)
        self.assertAlmostEqual(metrics.gross_margin, metrics.allowed - metrics.acquisition)
        self.assertAlmostEqual(metrics.net_margin, metrics.allowed * (1 - 0.02 * 0.80) - metrics.acquisition)
        self.assertAlmostEqual(metrics.denial_exposure, 0.0)

    def test_pricing_metrics_use_real_acquisition_cost_when_provided(self) -> None:
        metrics = calculate_pricing_metrics(
            total_dose_mg=400.0,
            billing_unit_size_mg=10.0,
            payment_limit=31.041,
            uplift_pct=0.06,
            acquisition_cost=1000.0,
            is_medicare=True,
            group_code="CO",
        )

        self.assertAlmostEqual(metrics.acquisition, 1000.0)
        self.assertAlmostEqual(metrics.gross_margin, 241.64)
        self.assertAlmostEqual(metrics.denial_exposure, 1000.0)

    def test_build_pricing_result_from_rows_uses_pricing_row_values(self) -> None:
        crosswalk_row = {"BillUnits": 10}
        pricing_row = {"facility_total": 31.041, "non_facility_total": 30.0}

        metrics = build_pricing_result_from_rows(
            total_dose_mg=400.0,
            crosswalk_row=crosswalk_row,
            pricing_row=pricing_row,
            site_of_service="facility",
            uplift_pct=0.06,
            is_medicare=True,
        )

        self.assertEqual(metrics.units, 40.0)
        self.assertAlmostEqual(metrics.payment_limit, 31.041)
        self.assertAlmostEqual(metrics.allowed, 1241.64)


if __name__ == "__main__":
    unittest.main()
