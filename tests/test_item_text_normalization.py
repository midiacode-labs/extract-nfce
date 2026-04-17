import unittest

from services.invoice_service import parse_expense_data


class ItemTextNormalizationTests(unittest.TestCase):
    def test_normalizes_item_description_and_expense_row(self):
        response = {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [],
                    "LineItemGroups": [
                        {
                            "LineItems": [
                                {
                                    "LineItemExpenseFields": [
                                        {
                                            "Type": {"Text": "ITEM"},
                                            "ValueDetection": {
                                                "Text": "TV TOSHIBA SMART\nTrib acros R$ 551.28"
                                            },
                                        },
                                        {
                                            "Type": {"Text": "EXPENSE_ROW"},
                                            "ValueDetection": {
                                                "Text": "395863 TV\nSMART   1.0000\t2,499.00"
                                            },
                                        },
                                    ]
                                }
                            ]
                        }
                    ],
                    "Blocks": [],
                }
            ]
        }

        data = parse_expense_data(response)

        self.assertEqual(
            data["items"][0]["description"],
            "TV TOSHIBA SMART Trib acros R$ 551.28",
        )
        self.assertEqual(
            data["items"][0]["expense_row"],
            "395863 TV SMART 1.0000 2,499.00",
        )

    def test_extracts_items_from_block_lines_when_textract_has_no_line_items(self):
        response = {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [],
                    "LineItemGroups": [],
                    "Blocks": [
                        {"BlockType": "LINE", "Text": "DADOS DO PRODUTO / SERVICOS"},
                        {
                            "BlockType": "LINE",
                            "Text": (
                                "COD. PROD. DESCRICAO DOS PRODUTOS / SERVICOS "
                                "NCM/SH CST CFOP UNIDADE QTDE V. UNITARIO V. TOTAL"
                            ),
                        },
                        {
                            "BlockType": "LINE",
                            "Text": (
                                "4002589 SMARTPHONE SAMSUNG G 85171300 060 5929 "
                                "UN 1,0000 999,000 999,00"
                            ),
                        },
                        {
                            "BlockType": "LINE",
                            "Text": (
                                "IMEI:356981210820889 Trib aprox R$ 166,73 "
                                "Federal, R$ 13,87 Estadual"
                            ),
                        },
                        {"BlockType": "LINE", "Text": "DADOS ADICIONAIS"},
                    ],
                }
            ]
        }

        data = parse_expense_data(response)

        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["code"], "4002589")
        self.assertEqual(data["items"][0]["description"], "SMARTPHONE SAMSUNG G")
        self.assertEqual(data["items"][0]["unit"], "UN")
        self.assertEqual(data["items"][0]["quantity"], "1,0000")
        self.assertEqual(data["items"][0]["unit_price"], "999,000")
        self.assertEqual(data["items"][0]["total_price"], "999,00")
        self.assertIn("IMEI:356981210820889", data["items"][0]["expense_row"])

    def test_extracts_items_from_multiline_block_rows(self):
        response = {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [],
                    "LineItemGroups": [],
                    "Blocks": [
                        {"BlockType": "LINE", "Text": "DADOS DO PRODUTO / SERVICOS"},
                        {
                            "BlockType": "LINE",
                            "Text": "4002589 SMARTPHONE SAMSUNG G",
                        },
                        {
                            "BlockType": "LINE",
                            "Text": "85171300 060 5929 UN 1,0000 999,000 999,00",
                        },
                        {
                            "BlockType": "LINE",
                            "Text": (
                                "IMEI:356981210820889 Trib aprox R$ 166,73 "
                                "Federal, R$ 13,87 Estadual"
                            ),
                        },
                        {"BlockType": "LINE", "Text": "DADOS ADICIONAIS"},
                    ],
                }
            ]
        }

        data = parse_expense_data(response)

        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["description"], "SMARTPHONE SAMSUNG G")
        self.assertEqual(data["items"][0]["ncm"], "85171300")
        self.assertEqual(data["items"][0]["cst"], "060")
        self.assertEqual(data["items"][0]["cfop"], "5929")
        self.assertEqual(data["items"][0]["unit"], "UN")

    def test_extracts_items_from_fragmented_cell_blocks(self):
        """When Textract splits each table cell into a separate LINE block with
        geometry, the cell-based fallback should reconstruct items."""
        def _cell(text, top, left):
            return {
                "BlockType": "LINE",
                "Text": text,
                "Geometry": {
                    "BoundingBox": {"Top": top, "Left": left, "Width": 0.05, "Height": 0.01}
                },
            }

        response = {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [],
                    "LineItemGroups": [{"LineItems": []}],
                    "Blocks": [
                        _cell("DADOS DO PRODUTO / SERVICOS", 0.4600, 0.0300),
                        # Header row
                        _cell("coe PROB", 0.4769, 0.0376),
                        _cell("DESCRICAO DOS PRODUTOS SERVICES", 0.4730, 0.1531),
                        _cell("MCM/R", 0.4717, 0.3626),
                        _cell("CFOP", 0.4707, 0.4322),
                        _cell("OTDE", 0.4695, 0.5145),
                        _cell("y UNITARIO", 0.4677, 0.5619),
                        _cell("v. TOTAL", 0.4667, 0.6397),
                        # Data row – cells spread across the table
                        _cell("85171300", 0.4829, 0.3591),
                        _cell("060", 0.4824, 0.4085),
                        _cell("5029", 0.4818, 0.4315),
                        _cell("UN", 0.4814, 0.4800),
                        _cell("1.0000", 0.4804, 0.5229),
                        _cell("999.0000", 0.4790, 0.5798),
                        _cell("999.00", 0.4774, 0.6683),
                        _cell("4020529", 0.4881, 0.0362),
                        _cell("SMARTPHONE SAMSUNG G", 0.4857, 0.0976),
                        # Tax info row
                        _cell(":368961210620699 Trib aprux R$ 166.73", 0.4917, 0.0968),
                        _cell("Federal R$ 132.87 Estadumi", 0.5002, 0.0980),
                        # End marker
                        _cell("DADOS ADICIONAIS", 0.5500, 0.0300),
                    ],
                }
            ]
        }

        data = parse_expense_data(response)

        self.assertEqual(len(data["items"]), 1)
        item = data["items"][0]
        self.assertEqual(item["code"], "4020529")
        self.assertEqual(item["description"], "SMARTPHONE SAMSUNG G")
        self.assertEqual(item["ncm"], "85171300")
        self.assertEqual(item["unit"], "UN")
        self.assertEqual(item["quantity"], "1.0000")
        self.assertEqual(item["unit_price"], "999.0000")
        self.assertEqual(item["total_price"], "999.00")

    def test_extracts_multiple_items_from_fragmented_cell_blocks(self):
        """Two items with fragmented cells should be separated correctly."""
        def _cell(text, top, left):
            return {
                "BlockType": "LINE",
                "Text": text,
                "Geometry": {
                    "BoundingBox": {"Top": top, "Left": left, "Width": 0.05, "Height": 0.01}
                },
            }

        response = {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [],
                    "LineItemGroups": [{"LineItems": []}],
                    "Blocks": [
                        _cell("DADOS DO PRODUTO / SERVICOS", 0.4600, 0.0300),
                        # Header cells
                        _cell("cón PROD.", 0.5409, 0.1046),
                        _cell("DESCRIÇÃO DOS PRODUTOS SERVIÇOS", 0.5373, 0.2127),
                        _cell("NCM/SU", 0.5378, 0.4009),
                        _cell("CIT", 0.5377, 0.4423),
                        _cell("cror", 0.5372, 0.4611),
                        _cell("UNIDADE", 0.5369, 0.4833),
                        _cell("OTDE", 0.5358, 0.5281),
                        _cell("v. UNITÁRIO", 0.5344, 0.5672),
                        _cell("v. TOTAL", 0.5350, 0.6325),
                        _cell("ALK", 0.5309, 0.8583),
                        # Item 1 data
                        _cell("05183200", 0.5500, 0.3968),
                        _cell("060", 0.5499, 0.4396),
                        _cell("5928", 0.5495, 0.4597),
                        _cell("UN", 0.5488, 0.4996),
                        _cell("1,0000", 0.5481, 0.5351),
                        _cell("119.0000", 0.5475, 0.5822),
                        _cell("119.00", 0.5472, 0.6543),
                        _cell("5168004", 0.5542, 0.1044),
                        _cell("PRANCHA TITANIUM BLU", 0.5510, 0.1626),
                        _cell("Tdb eprox Estadual", 0.5571, 0.1631),
                        # Item 2 data
                        _cell("05163100", 0.5651, 0.3970),
                        _cell("080", 0.5650, 0.4392),
                        _cell("5929", 0.5646, 0.4596),
                        _cell("UN", 0.5642, 0.4988),
                        _cell("1:0000", 0.5638, 0.5347),
                        _cell("139.0000", 0.5632, 0.5826),
                        _cell("130.00", 0.5619, 0.6539),
                        _cell("3925790", 0.5701, 0.1061),
                        _cell("SECADOR DE CABELO MO", 0.5667, 0.1636),
                        _cell("Trip sprox R$: 26,69 Federal R$:25,02 Estadual", 0.5728, 0.1629),
                        # End marker
                        _cell("DADOS ADICIONAIS", 0.6500, 0.0300),
                    ],
                }
            ]
        }

        data = parse_expense_data(response)

        self.assertEqual(len(data["items"]), 2)
        item1, item2 = data["items"]
        self.assertEqual(item1["code"], "5168004")
        self.assertEqual(item1["description"], "PRANCHA TITANIUM BLU")
        self.assertEqual(item1["unit_price"], "119.0000")
        self.assertEqual(item1["total_price"], "119.00")
        self.assertEqual(item2["code"], "3925790")
        self.assertEqual(item2["description"], "SECADOR DE CABELO MO")
        self.assertEqual(item2["unit_price"], "139.0000")
        self.assertEqual(item2["total_price"], "130.00")


if __name__ == "__main__":
    unittest.main()
