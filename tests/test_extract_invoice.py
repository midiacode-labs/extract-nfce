import unittest

from services.invoice_service import parse_expense_data


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

        self.assertEqual(data["emitter"].get("cnpj"), "12.345.678/0001-90")
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

    def test_extracts_consumer_name_from_destinatario_block_after_danfe_title(self):
        response = {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [],
                    "LineItemGroups": [],
                    "Blocks": [
                        {
                            "BlockType": "LINE",
                            "Text": "DOCUMENTO AUXILIAR DA NOTA FISCAL DE CONSUMIDOR ELETRONICA",
                        },
                        {"BlockType": "LINE", "Text": "CARREFOUR COM. E IND. LTDA"},
                        {"BlockType": "LINE", "Text": "DESTINATARIO/REMETENTE"},
                        {"BlockType": "LINE", "Text": "SAMUEL DE JESUS PEREIRA"},
                        {"BlockType": "LINE", "Text": "659.793.705-87"},
                        {"BlockType": "LINE", "Text": "RUA DA FONTE, 109"},
                        {"BlockType": "LINE", "Text": "JARDIM PORTAL I E II"},
                        {"BlockType": "LINE", "Text": "02327-180"},
                        {"BlockType": "LINE", "Text": "Sao Paulo"},
                        {"BlockType": "LINE", "Text": "SP"},
                    ],
                }
            ]
        }

        data = parse_expense_data(response)

        self.assertEqual(data["consumer"].get("name"), "SAMUEL DE JESUS PEREIRA")
        self.assertEqual(data["consumer"].get("document"), "659.793.705-87")

    def test_ignores_consumer_label_line_and_extracts_actual_name(self):
        response = {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [],
                    "LineItemGroups": [],
                    "Blocks": [
                        {"BlockType": "LINE", "Text": "DESTINATARIO/REMETENTE"},
                        {"BlockType": "LINE", "Text": "NOME / RAZAO SOCIAL"},
                        {"BlockType": "LINE", "Text": "KENIA ARLEO"},
                        {"BlockType": "LINE", "Text": "091.803.826-06"},
                        {"BlockType": "LINE", "Text": "AV MORVAN DIAS DE FIGUEIREDO, 3177"},
                        {"BlockType": "LINE", "Text": "VILA GUILHERME"},
                        {"BlockType": "LINE", "Text": "02063-000"},
                        {"BlockType": "LINE", "Text": "Sao Paulo"},
                        {"BlockType": "LINE", "Text": "SP"},
                    ],
                }
            ]
        }

        data = parse_expense_data(response)

        self.assertEqual(data["consumer"].get("name"), "KENIA ARLEO")
        self.assertEqual(data["consumer"].get("document"), "091.803.826-06")

    def test_ignores_issue_date_labels_inside_consumer_window(self):
        response = {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [],
                    "LineItemGroups": [],
                    "Blocks": [
                        {"BlockType": "LINE", "Text": "DESTINATARIO/REMETENTE"},
                        {"BlockType": "LINE", "Text": "DATA DA EMISSAO"},
                        {"BlockType": "LINE", "Text": "KENIA ARLEO"},
                        {"BlockType": "LINE", "Text": "091.803.826-06"},
                        {"BlockType": "LINE", "Text": "AV MORVAN DIAS DE FIGUEIREDO, 3177"},
                        {"BlockType": "LINE", "Text": "VILA GUILHERME"},
                        {"BlockType": "LINE", "Text": "Sao Paulo"},
                        {"BlockType": "LINE", "Text": "SP"},
                    ],
                }
            ]
        }

        data = parse_expense_data(response)

        self.assertEqual(data["consumer"].get("name"), "KENIA ARLEO")
        self.assertNotEqual(data["consumer"].get("name"), "DATA DA EMISSAO")

    def test_block_consumer_name_overrides_wrong_summary_name_equal_to_emitter(self):
        response = {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [
                        {
                            "Type": {"Text": "VENDOR_NAME"},
                            "ValueDetection": {"Text": "CARREFOUR COM. E IND. LTDA"},
                        },
                        {
                            "Type": {"Text": "RECEIVER_NAME"},
                            "ValueDetection": {"Text": "CARREFOUR COM. E IND. LTDA"},
                        },
                    ],
                    "LineItemGroups": [],
                    "Blocks": [
                        {"BlockType": "LINE", "Text": "DESTINATARIO/REMETENTE"},
                        {"BlockType": "LINE", "Text": "MARLENE DE LIMA"},
                        {"BlockType": "LINE", "Text": "812.558.538-91"},
                        {"BlockType": "LINE", "Text": "RUA MOREIRA DE VASCONCELOS, 198"},
                        {"BlockType": "LINE", "Text": "VILA MARIA ALTA"},
                        {"BlockType": "LINE", "Text": "02131-090"},
                        {"BlockType": "LINE", "Text": "Sao Paulo"},
                        {"BlockType": "LINE", "Text": "SP"},
                    ],
                }
            ]
        }

        data = parse_expense_data(response)

        self.assertEqual(data["emitter"].get("company_name"), "CARREFOUR COM. E IND. LTDA")
        self.assertEqual(data["consumer"].get("name"), "MARLENE DE LIMA")
        self.assertNotEqual(data["consumer"].get("name"), "CARREFOUR COM. E IND. LTDA")


if __name__ == "__main__":
    unittest.main()
