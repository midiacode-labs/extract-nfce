import unittest

from services.cost_estimation import (
    build_textract_usage_summary,
    estimate_textract_analyze_expense_cost_usd,
)


class CostEstimationTests(unittest.TestCase):
    def test_estimate_textract_analyze_expense_cost_includes_37_percent_surcharge(self):
        self.assertAlmostEqual(
            estimate_textract_analyze_expense_cost_usd(1),
            0.0137,
            places=7,
        )

    def test_build_textract_usage_summary_includes_provider_details(self):
        usage = build_textract_usage_summary(page_count=2, region="us-east-1")

        self.assertEqual(usage["provider"], "aws_textract")
        self.assertEqual(usage["api"], "analyze_expense")
        self.assertEqual(usage["page_count"], 2)
        self.assertEqual(usage["region"], "us-east-1")
        self.assertAlmostEqual(usage["estimated_cost_usd"], 0.0274, places=7)


if __name__ == "__main__":
    unittest.main()
