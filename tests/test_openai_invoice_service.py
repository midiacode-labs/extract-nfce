import json
import os
import tempfile
import unittest

from services.openai_invoice_service import OpenAIInvoiceExtractionService


class _FakeResponses:
    def __init__(self, payload, usage=None):
        self.payload = payload
        self.usage = usage
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        usage = None
        if self.usage is not None:
            usage = type("Usage", (), self.usage)()
        return type(
            "OpenAIResponse",
            (),
            {"output_text": json.dumps(self.payload), "usage": usage},
        )()


class _FakeClient:
    def __init__(self, payload, usage=None):
        self.responses = _FakeResponses(payload, usage=usage)


class OpenAIInvoiceExtractionServiceTests(unittest.TestCase):
    def test_process_image_writes_structured_json_and_records_usage(self):
        payload = {
            "emitter": {
                "company_name": "MAGAZINE LUIZA S/A",
                "cnpj": "45.543.915/0064-65",
            },
            "identification": {
                "number": "855600",
                "series": "1",
                "issue_date": "12/04/2026",
                "issue_time": "16:26:50",
                "authorization_protocol": "135261385400803",
                "authorization_datetime": "12/04/2026 16:26:50",
            },
            "consumer": {
                "name": "NILSON SERGIO DE PADUA OLIVEI",
                "document": "157.132.308-21",
                "street": "RUA HILARIO PINTO DE ALMEIDA, 185",
                "neighborhood": "JARDIM IPANEMA ZONA SUL",
                "city": "SAO PAULO",
                "state": "sp",
            },
            "items": [
                {
                    "code": "4014057",
                    "description": "TV 43 TOSHIBA SMART Trib aprox R$ 12,00",
                    "quantity": "1",
                    "unit_price": "1.599,00",
                    "total_price": "1.599,00",
                    "cfop": "5102",
                    "ncm": "85287200",
                }
            ],
            "totals": {"total_invoice_value": "1.599,00"},
            "access_key": {"value": "35260455543915006465550010008556001365692679"},
            "tax_calculation": {"approximate_tax": "495,21"},
            "transport": {"freight_type": "9 - Sem Frete"},
            "fiscal_message": {},
            "additional_info": {},
        }
        usage = {"input_tokens": 1200, "output_tokens": 450, "total_tokens": 1650}
        service = OpenAIInvoiceExtractionService(
            api_key="test-key",
            client=_FakeClient(payload, usage=usage),
            model="gpt-4.1-mini",
        )

        dataset_image = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "dataset",
            "nfce_001.jpeg",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "nfce_001.json")
            extracted_data, saved_path = service.process_image(dataset_image, output=output_path)

            self.assertEqual(saved_path, output_path)
            self.assertTrue(os.path.exists(saved_path))
            self.assertEqual(extracted_data["emitter"]["company_name"], "MAGAZINE LUIZA S/A")
            self.assertEqual(extracted_data["identification"]["number"], "855600")
            self.assertEqual(
                extracted_data["consumer"]["address"],
                "RUA HILARIO PINTO DE ALMEIDA, 185, SAO PAULO, JARDIM IPANEMA ZONA SUL, SP",
            )
            self.assertEqual(extracted_data["items"][0]["description"], "TV 43 TOSHIBA SMART")
            self.assertIn("is_valid", extracted_data["access_key"])
            self.assertEqual(service.get_last_usage()["total_tokens"], 1650)
            self.assertAlmostEqual(
                service.get_last_usage()["estimated_cost_usd"],
                0.001644,
                places=7,
            )

            create_call = service.client.responses.calls[0]
            self.assertEqual(create_call["model"], "gpt-4.1-mini")
            image_payload = create_call["input"][1]["content"][1]["image_url"]
            self.assertTrue(image_payload.startswith("data:image/jpeg;base64,"))

    def test_normalize_extracted_data_sanitizes_item_description_like_openai_qualification(self):
        service = OpenAIInvoiceExtractionService(api_key="test-key", client=_FakeClient({}))

        extracted_data = service.normalize_extracted_data(
            {
                "emitter": {},
                "identification": {},
                "consumer": {},
                "items": [
                    {
                        "description": "PRANCHA TITANIUM BLU Trib aprox R",
                        "quantity": "1",
                        "unit_price": "139,90",
                    }
                ],
                "totals": {},
                "access_key": {},
                "tax_calculation": {},
                "transport": {},
                "fiscal_message": {},
                "additional_info": {},
            }
        )

        self.assertEqual(extracted_data["items"][0]["description"], "PRANCHA TITANIUM BLU")

    def test_normalize_extracted_data_removes_residual_fiscal_noise_from_item_description(self):
        service = OpenAIInvoiceExtractionService(api_key="test-key", client=_FakeClient({}))

        extracted_data = service.normalize_extracted_data(
            {
                "emitter": {},
                "identification": {},
                "consumer": {},
                "items": [
                    {
                        "description": (
                            "TV 65 SAMSUNG UN65BU7350WXZD Tp de apre. "
                            "R 9,10 / R 60,03 Refapefun."
                        )
                    }
                ],
                "totals": {},
                "access_key": {},
                "tax_calculation": {},
                "transport": {},
                "fiscal_message": {},
                "additional_info": {},
            }
        )

        self.assertEqual(
            extracted_data["items"][0]["description"],
            "TV 65 SAMSUNG UN65BU7350WXZD",
        )

    def test_normalize_extracted_data_flattens_nested_consumer_address_fields(self):
        service = OpenAIInvoiceExtractionService(api_key="test-key", client=_FakeClient({}))

        extracted_data = service.normalize_extracted_data(
            {
                "emitter": {},
                "identification": {},
                "consumer": {
                    "name": "NILSON SERGIO DE PADUA OLIVEI",
                    "cpf": "157.132.308-21",
                    "endereco": {
                        "logradouro": "RUA HILARIO PINTO DE ALMEIDA",
                        "numero": "185",
                        "bairro": "JARDIM IPANEMA ZONA SUL",
                        "cidade": "SAO PAULO",
                        "estado": "sp",
                        "cep": "04777-001",
                    },
                },
                "items": [],
                "totals": {},
                "access_key": {},
                "tax_calculation": {},
                "transport": {},
                "fiscal_message": {},
                "additional_info": {},
            }
        )

        self.assertEqual(
            extracted_data["consumer"],
            {
                "name": "NILSON SERGIO DE PADUA OLIVEI",
                "document": "157.132.308-21",
                "street": "RUA HILARIO PINTO DE ALMEIDA, 185",
                "zip_code": "04777-001",
                "city": "SAO PAULO",
                "neighborhood": "JARDIM IPANEMA ZONA SUL",
                "state": "SP",
                "address": (
                    "RUA HILARIO PINTO DE ALMEIDA, 185, 04777-001, SAO PAULO, "
                    "JARDIM IPANEMA ZONA SUL, SP"
                ),
            },
        )


if __name__ == "__main__":
    unittest.main()
