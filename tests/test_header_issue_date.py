import unittest

from services.invoice_service import parse_expense_data


class HeaderIssueDateTests(unittest.TestCase):
    def test_extracts_invoice_issue_date_into_header(self):
        response = {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [
                        {
                            "Type": {"Text": "INVOICE_RECEIPT_DATE"},
                            "ValueDetection": {"Text": "12/04/2026"},
                        }
                    ],
                    "LineItemGroups": [],
                    "Blocks": [],
                }
            ]
        }

        data = parse_expense_data(response)

        self.assertEqual(data["identification"].get("issue_date"), "12/04/2026")

    def test_extracts_invoice_issue_date_from_generic_ocr_fields(self):
        response = {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [
                        {
                            "Type": {"Text": "OTHER"},
                            "ValueDetection": {"Text": "12/04/2026"},
                        }
                    ],
                    "LineItemGroups": [],
                    "Blocks": [
                        {"BlockType": "LINE", "Text": "PROTOCOLO DE AUTORIZACAO"},
                        {
                            "BlockType": "LINE",
                            "Text": "135261385038708 12/04/2026 15:42:50",
                        },
                    ],
                }
            ]
        }

        data = parse_expense_data(response)

        self.assertEqual(data["identification"].get("issue_date"), "12/04/2026")


if __name__ == "__main__":
    unittest.main()
