import unittest

from extract_invoice import parse_expense_data


class ParseExpenseDataTests(unittest.TestCase):
    def test_extracts_issuer_cnpj_into_header_from_ocr_lines(self):
        response = {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [],
                    "LineItemGroups": [],
                    "Blocks": [
                        {"BlockType": "LINE", "Text": "CONSUMIDOR FINAL"},
                        {"BlockType": "LINE", "Text": "CNPJ: 12.345.678/0001-90"},
                        {"BlockType": "LINE", "Text": "RUA DAS FLORES, 123"},
                    ],
                }
            ]
        }

        data = parse_expense_data(response)

        self.assertEqual(data["header"].get("cnpj"), "12.345.678/0001-90")
        self.assertIsNone(data["consumer"].get("document"))

    def test_extracts_consumer_address_from_summary_fields(self):
        response = {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [
                        {
                            "Type": {"Text": "RECEIVER_NAME"},
                            "ValueDetection": {"Text": "JOAO SILVA"},
                        },
                        {
                            "Type": {"Text": "RECEIVER_VAT_NUMBER"},
                            "ValueDetection": {"Text": "123.456.789-00"},
                        },
                        {
                            "Type": {"Text": "RECEIVER_ADDRESS"},
                            "ValueDetection": {
                                "Text": "RUA DAS FLORES, 123 - CENTRO - SAO PAULO/SP"
                            },
                        },
                    ],
                    "LineItemGroups": [],
                    "Blocks": [],
                }
            ]
        }

        data = parse_expense_data(response)

        self.assertEqual(
            data["consumer"].get("address"),
            "RUA DAS FLORES, 123 - CENTRO - SAO PAULO/SP",
        )

    def test_extracts_consumer_address_from_text_fallback(self):
        response = {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [],
                    "LineItemGroups": [],
                    "Blocks": [
                        {"BlockType": "LINE", "Text": "CONSUMIDOR"},
                        {"BlockType": "LINE", "Text": "MARIA OLIVEIRA"},
                        {"BlockType": "LINE", "Text": "CPF: 987.654.321-00"},
                        {"BlockType": "LINE", "Text": "AV BRASIL, 500 APTO 12"},
                        {"BlockType": "LINE", "Text": "CENTRO - RIO DE JANEIRO/RJ"},
                    ],
                }
            ]
        }

        data = parse_expense_data(response)

        self.assertEqual(
            data["consumer"].get("address"),
            "AV BRASIL, 500 APTO 12, CENTRO - RIO DE JANEIRO/RJ",
        )


if __name__ == "__main__":
    unittest.main()
