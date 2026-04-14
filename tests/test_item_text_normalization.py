import unittest

from extract_invoice import parse_expense_data


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


if __name__ == "__main__":
    unittest.main()
