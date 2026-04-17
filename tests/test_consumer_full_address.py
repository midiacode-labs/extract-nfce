import unittest

from services.invoice_service import parse_expense_data


class ConsumerFullAddressTests(unittest.TestCase):
    def test_extracts_full_consumer_address_and_ignores_dates(self):
        response = {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [],
                    "LineItemGroups": [],
                    "Blocks": [
                        {"BlockType": "LINE", "Text": "DESTINATARIO/REMETENTE"},
                        {"BlockType": "LINE", "Text": "CHIP EFF"},
                        {"BlockType": "LINE", "Text": "CPF: 030.364.455-95"},
                        {"BlockType": "LINE", "Text": "12/04/2026"},
                        {"BlockType": "LINE", "Text": "RONILDA VIEIRA PRADO OLIVEIRA"},
                        {"BlockType": "LINE", "Text": "DATA CALENTISA"},
                        {"BlockType": "LINE", "Text": "CIT"},
                        {"BlockType": "LINE", "Text": "02363-320"},
                        {"BlockType": "LINE", "Text": "12/04/2026"},
                        {"BlockType": "LINE", "Text": "RUA ORQUIDEA, 109"},
                        {"BlockType": "LINE", "Text": "IN"},
                        {"BlockType": "LINE", "Text": "15:41"},
                        {"BlockType": "LINE", "Text": "SP"},
                        {"BlockType": "LINE", "Text": "Sao Paulo"},
                        {"BlockType": "LINE", "Text": "JARDIM FLOR DE MAIO"},
                    ],
                }
            ]
        }

        data = parse_expense_data(response)

        self.assertEqual(data["consumer"].get("name"), "RONILDA VIEIRA PRADO OLIVEIRA")
        self.assertEqual(data["consumer"].get("document"), "030.364.455-95")
        self.assertEqual(data["consumer"].get("street"), "RUA ORQUIDEA, 109")
        self.assertEqual(data["consumer"].get("zip_code"), "02363-320")
        self.assertEqual(data["consumer"].get("city"), "Sao Paulo")
        self.assertEqual(data["consumer"].get("neighborhood"), "JARDIM FLOR DE MAIO")
        self.assertEqual(data["consumer"].get("state"), "SP")
        self.assertEqual(
            data["consumer"].get("address"),
            "RUA ORQUIDEA, 109, 02363-320, Sao Paulo, JARDIM FLOR DE MAIO, SP",
        )

    def test_ignores_destination_form_labels_for_address_fields(self):
        response = {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [],
                    "LineItemGroups": [],
                    "Blocks": [
                        {"BlockType": "LINE", "Text": "DESTINATARIO/REMETENTE"},
                        {"BlockType": "LINE", "Text": "NOME / RAZAO SOCIAL"},
                        {"BlockType": "LINE", "Text": "KENIA ARLEO"},
                        {"BlockType": "LINE", "Text": "CPF/CNPJ"},
                        {"BlockType": "LINE", "Text": "091.803.826-06"},
                        {"BlockType": "LINE", "Text": "ENDERECO"},
                        {"BlockType": "LINE", "Text": "AV MORVAN DIAS DE FIGUEIREDO, 3177"},
                        {"BlockType": "LINE", "Text": "BAIRRO/DISTRITO"},
                        {"BlockType": "LINE", "Text": "VILA GUILHERME"},
                        {"BlockType": "LINE", "Text": "CEP"},
                        {"BlockType": "LINE", "Text": "02063-000"},
                        {"BlockType": "LINE", "Text": "MUNICIPIO"},
                        {"BlockType": "LINE", "Text": "Sao Paulo"},
                        {"BlockType": "LINE", "Text": "UF"},
                        {"BlockType": "LINE", "Text": "SP"},
                        {"BlockType": "LINE", "Text": "FONE/FAX"},
                    ],
                }
            ]
        }

        data = parse_expense_data(response)

        self.assertEqual(data["consumer"].get("name"), "KENIA ARLEO")
        self.assertEqual(data["consumer"].get("document"), "091.803.826-06")
        self.assertEqual(
            data["consumer"].get("street"),
            "AV MORVAN DIAS DE FIGUEIREDO, 3177",
        )
        self.assertEqual(data["consumer"].get("neighborhood"), "VILA GUILHERME")
        self.assertEqual(data["consumer"].get("zip_code"), "02063-000")
        self.assertEqual(data["consumer"].get("city"), "Sao Paulo")
        self.assertEqual(data["consumer"].get("state"), "SP")
        self.assertEqual(
            data["consumer"].get("address"),
            "AV MORVAN DIAS DE FIGUEIREDO, 3177, 02063-000, Sao Paulo, VILA GUILHERME, SP",
        )


if __name__ == "__main__":
    unittest.main()
