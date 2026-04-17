import json
import os
import tempfile
import unittest

from services.invoice_qualification_service import InvoiceQualificationService


class _FakeResponses:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("OpenAIResponse", (), {"output_text": json.dumps(self.payload)})()


class _FakeClient:
    def __init__(self, payload):
        self.responses = _FakeResponses(payload)


class InvoiceQualificationServiceTests(unittest.TestCase):
    def test_qualifies_consumer_address_and_item_descriptions(self):
        extracted_data = {
            "header": {"vendor": "MERCADO MODELO"},
            "consumer": {
                "name": "JOAO SILVA",
                "zip_code": "01310-100",
                "street": "AV PAULISTA",
                "city": "SAOO PAULO",
                "state": "SP",
                "address": "AV PAULISTA, 01310-100, SAOO PAULO, SP",
            },
            "items": [
                {
                    "description": "CAFE 500G Trib aprox R$ 1,23",
                    "total_price": "12,90",
                    "quantity": "1",
                }
            ],
        }
        qualified_payload = {
            "consumer": {
                "zip_code": "01310-100",
                "street": "Avenida Paulista",
                "city": "São Paulo",
                "state": "SP",
                "address": "Avenida Paulista, 01310-100, São Paulo, SP",
            },
            "items": [{"description": "CAFE 500G"}],
        }

        fake_client = _FakeClient(qualified_payload)
        service = InvoiceQualificationService(api_key="test-key", client=fake_client)

        qualified_data = service.qualify_data(extracted_data)

        self.assertEqual(qualified_data["consumer"]["city"], "São Paulo")
        self.assertEqual(qualified_data["consumer"]["street"], "Avenida Paulista")
        self.assertEqual(qualified_data["items"][0]["description"], "CAFE 500G")
        self.assertEqual(qualified_data["items"][0]["total_price"], "12,90")
        self.assertEqual(len(fake_client.responses.calls), 1)

    def test_qualify_file_writes_a_new_qualified_json(self):
        extracted_data = {
            "emitter": {"company_name": "MERCADO MODELO"},
            "consumer": {"zip_code": "01310-100"},
            "items": [{"description": "ARROZ Trib aprox R$ 0,99"}],
        }
        qualified_payload = {
            "consumer": {"zip_code": "01310-100", "city": "São Paulo"},
            "items": [{"description": "ARROZ"}],
        }

        service = InvoiceQualificationService(
            api_key="test-key",
            client=_FakeClient(qualified_payload),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "nfce_001.json")
            with open(input_path, "w", encoding="utf-8") as file_handle:
                json.dump(extracted_data, file_handle)

            qualified_data, qualified_path = service.qualify_file(input_path)

            self.assertTrue(qualified_path.endswith("nfce_001_qualified.json"))
            self.assertTrue(os.path.exists(qualified_path))
            self.assertEqual(qualified_data["items"][0]["description"], "ARROZ")

    def test_fix_item_total_price_recalculates_when_wrong(self):
        service = InvoiceQualificationService(api_key=None, client=None)
        extracted_data = {
            "consumer": {},
            "items": [
                {
                    "description": "SECADOR DE CABELO",
                    "quantity": "1,0000",
                    "unit_price": "139.0000",
                    "total_price": "130.00",
                },
                {
                    "description": "PRANCHA TITANIUM",
                    "quantity": "2,0000",
                    "unit_price": "119.0000",
                    "total_price": "238.00",
                },
            ],
        }
        qualified = service.apply_local_fixes(extracted_data)

        self.assertEqual(qualified["items"][0]["total_price"], "139.00")
        # Item 2 already matches (2 * 119 = 238), should remain unchanged
        self.assertEqual(qualified["items"][1]["total_price"], "238.00")

    def test_fix_item_total_price_preserves_brazilian_format(self):
        service = InvoiceQualificationService(api_key=None, client=None)
        extracted_data = {
            "consumer": {},
            "items": [
                {
                    "description": "CAFE 500G",
                    "quantity": "3",
                    "unit_price": "12,90",
                    "total_price": "35,70",
                },
            ],
        }
        qualified = service.apply_local_fixes(extracted_data)

        self.assertEqual(qualified["items"][0]["total_price"], "38,70")

    def test_fix_item_total_price_skips_when_fields_missing(self):
        service = InvoiceQualificationService(api_key=None, client=None)
        extracted_data = {
            "consumer": {},
            "items": [
                {
                    "description": "ARROZ",
                    "total_price": "9,99",
                },
            ],
        }
        qualified = service.apply_local_fixes(extracted_data)

        # No unit_price/quantity, so total_price stays as-is
        self.assertEqual(qualified["items"][0]["total_price"], "9,99")


if __name__ == "__main__":
    unittest.main()
