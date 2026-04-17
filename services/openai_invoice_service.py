import base64
import json
import logging
import mimetypes
import os
from typing import Any, Dict, Optional, Tuple

try:
    from openai import OpenAI
except ImportError:  # Allows import in environments without the SDK
    OpenAI = None

from services.invoice_qualification_service import InvoiceQualificationService
from services.invoice_service import InvoiceExtractionService
from services.openai_response_utils import (
    build_usage_summary,
    extract_response_text,
    parse_json_response,
)


class OpenAIInvoiceExtractionService:
    """Extracts a full invoice JSON directly from an image using the OpenAI SDK."""

    DEFAULT_MODEL = "gpt-4.1-mini"
    TOP_LEVEL_OBJECT_KEYS = (
        "emitter",
        "identification",
        "consumer",
        "totals",
        "access_key",
        "tax_calculation",
        "transport",
        "fiscal_message",
        "additional_info",
    )

    def __init__(
        self,
        api_key: Optional[str] = None,
        client: Any = None,
        model: Optional[str] = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model or os.environ.get("OPENAI_EXTRACTION_MODEL") or os.environ.get(
            "OPENAI_MODEL",
            self.DEFAULT_MODEL,
        )
        self.client = client if client is not None else self._create_client()
        self.last_usage: Optional[Dict[str, Any]] = None

    def _create_client(self) -> Any:
        """Creates the OpenAI client when the SDK and API key are available."""
        if OpenAI is None:
            logging.warning("OpenAI SDK is not installed.")
            return None

        if not self.api_key:
            logging.warning("OPENAI_API_KEY is not configured.")
            return None

        return OpenAI(api_key=self.api_key)

    @staticmethod
    def resolve_output_path(input_file: str, output: Optional[str] = None) -> str:
        """Builds the destination JSON path inside the project output folder."""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_dir = os.path.join(project_root, "output")
        os.makedirs(output_dir, exist_ok=True)

        if not output:
            base_name = os.path.splitext(os.path.basename(input_file))[0]
            return os.path.join(output_dir, f"{base_name}.json")

        if not os.path.dirname(output):
            return os.path.join(output_dir, output)

        return output

    @staticmethod
    def _encode_image_as_data_url(input_file: str) -> str:
        """Reads the image file and returns it as a base64 data URL."""
        with open(input_file, "rb") as file_handle:
            image_bytes = file_handle.read()

        mime_type = mimetypes.guess_type(input_file)[0] or "image/jpeg"
        encoded = base64.b64encode(image_bytes).decode("utf-8")
        return f"data:{mime_type};base64,{encoded}"

    @classmethod
    def _build_response_format(cls) -> Dict[str, Any]:
        """Builds the JSON response format requested from the model."""
        return {
            "format": {
                "type": "json_object",
            }
        }

    def build_messages(self, input_file: str) -> list[Dict[str, Any]]:
        """Builds the image + instruction payload for the OpenAI Responses API."""
        instructions = (
            "You extract Brazilian NFC-e/NF-e invoices from a single image and return only JSON. "
            "Always return the complete project schema with the top-level keys emitter, "
            "identification, consumer, items, totals, access_key, tax_calculation, transport, "
            "fiscal_message, and additional_info. "
            "Keep values grounded in the document. Do not invent data. "
            "Prefer strings for textual and monetary fields exactly as they appear on the note. "
            "Use empty objects or an empty items array when a section cannot be read. "
            "For consumer address details, always populate flat consumer fields named street, "
            "zip_code, city, neighborhood, state, and address whenever visible. "
            "Do not keep the consumer address only inside a nested address or endereco object. "
            "For identification, include at least number, series, issue_date, issue_time, "
            "page, nature_of_operation, authorization_protocol, and "
            "authorization_datetime whenever visible. "
            "For item rows, extract description, quantity, unit_price, total_price, "
            "code, NCM, CFOP, "
            "and other visible item-level tax or classification fields when present."
        )
        return [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": instructions}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Extract the invoice into the requested JSON schema.",
                    },
                    {
                        "type": "input_image",
                        "image_url": self._encode_image_as_data_url(input_file),
                    },
                ],
            },
        ]

    @staticmethod
    def _normalize_nested_value(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): OpenAIInvoiceExtractionService._normalize_nested_value(item)
                for key, item in value.items()
                if key is not None
            }
        if isinstance(value, list):
            return [OpenAIInvoiceExtractionService._normalize_nested_value(item) for item in value]
        if isinstance(value, str):
            return InvoiceExtractionService.normalize_text(value)
        return value

    @staticmethod
    def _first_non_empty(mapping: Dict[str, Any], *keys: str) -> Any:
        """Returns the first non-empty value found among the provided keys."""
        for key in keys:
            value = mapping.get(key)
            if value not in (None, "", []):
                return value
        return None

    def _normalize_consumer_data(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Flattens consumer aliases and nested address structures into project fields."""
        consumer_payload = payload.get("consumer", {})
        if not isinstance(consumer_payload, dict):
            consumer_payload = {}

        normalized_consumer = self._normalize_nested_value(consumer_payload)
        if not isinstance(normalized_consumer, dict):
            normalized_consumer = {}

        nested_address = self._first_non_empty(normalized_consumer, "address", "endereco")
        if not isinstance(nested_address, dict):
            nested_address = {}

        street_value = self._first_non_empty(
            normalized_consumer,
            "street",
            "logradouro",
            "address_line",
        )
        address_number = self._first_non_empty(normalized_consumer, "number", "numero")

        nested_street = self._first_non_empty(
            nested_address,
            "street",
            "logradouro",
            "address_line",
        )
        nested_number = self._first_non_empty(nested_address, "number", "numero")

        street = street_value or nested_street
        number = address_number or nested_number
        if street and number and str(number) not in str(street):
            street = f"{street}, {number}"

        consumer: Dict[str, Any] = {}
        field_map = {
            "name": self._first_non_empty(normalized_consumer, "name", "nome", "razao_social"),
            "document": self._first_non_empty(
                normalized_consumer,
                "document",
                "cpf_cnpj",
                "cpf/cnpj",
                "cpf",
                "cnpj",
            ),
            "street": street,
            "zip_code": self._first_non_empty(
                normalized_consumer,
                "zip_code",
                "cep",
            )
            or self._first_non_empty(nested_address, "zip_code", "cep"),
            "city": self._first_non_empty(normalized_consumer, "city", "cidade")
            or self._first_non_empty(nested_address, "city", "cidade"),
            "neighborhood": self._first_non_empty(
                normalized_consumer,
                "neighborhood",
                "bairro",
                "district",
            )
            or self._first_non_empty(nested_address, "neighborhood", "bairro", "district"),
            "state": self._first_non_empty(normalized_consumer, "state", "estado", "uf")
            or self._first_non_empty(nested_address, "state", "estado", "uf"),
            "address": self._first_non_empty(normalized_consumer, "address"),
            "phone": self._first_non_empty(normalized_consumer, "phone", "telefone"),
        }

        for key, value in field_map.items():
            if value not in (None, "", []):
                consumer[key] = value

        if isinstance(consumer.get("state"), str):
            consumer["state"] = consumer["state"].upper()
        if consumer and (
            not consumer.get("address") or isinstance(consumer.get("address"), dict)
        ):
            consumer.pop("address", None)
            InvoiceExtractionService.compose_consumer_address(consumer)

        return consumer

    def normalize_extracted_data(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Normalizes the model output into the stable project schema."""
        normalized: Dict[str, Any] = {key: {} for key in self.TOP_LEVEL_OBJECT_KEYS}
        normalized["items"] = []

        for key in self.TOP_LEVEL_OBJECT_KEYS:
            if key == "consumer":
                continue
            value = payload.get(key, {})
            if isinstance(value, dict):
                normalized[key] = self._normalize_nested_value(value)

        normalized["consumer"] = self._normalize_consumer_data(payload)

        items = payload.get("items", [])
        if isinstance(items, list):
            normalized["items"] = [
                self._normalize_nested_value(item) for item in items if isinstance(item, dict)
            ]
            for item in normalized["items"]:
                description = item.get("description")
                if isinstance(description, str) and description:
                    item["description"] = InvoiceQualificationService.sanitize_item_description(
                        description
                    )

        access_key_value = normalized.get("access_key", {}).get("value")
        if isinstance(access_key_value, str):
            normalized["access_key"]["is_valid"] = InvoiceExtractionService.validate_access_key(
                access_key_value
            )

        return normalized

    def get_last_usage(self) -> Optional[Dict[str, Any]]:
        """Returns the token usage summary from the last OpenAI extraction call."""
        return self.last_usage

    def process_image(
        self,
        input_file: str,
        output: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """Processes an invoice image and persists the extracted JSON output."""
        if self.client is None:
            raise RuntimeError(
                "OpenAI image extraction requires the OpenAI SDK and OPENAI_API_KEY."
            )

        logging.info("Starting OpenAI extraction for file %s", input_file)
        output_path = self.resolve_output_path(input_file, output)
        response = self.client.responses.create(
            model=self.model,
            input=self.build_messages(input_file),
            text=self._build_response_format(),
        )
        self.last_usage = build_usage_summary(response, self.model)

        response_text = extract_response_text(response)
        final_data = self.normalize_extracted_data(parse_json_response(response_text))

        with open(output_path, "w", encoding="utf-8") as file_handle:
            json.dump(final_data, file_handle, indent=4, ensure_ascii=False)

        if self.last_usage:
            logging.info(
                "OpenAI extraction usage: input=%s output=%s total=%s estimated_cost_usd=%s",
                self.last_usage.get("input_tokens"),
                self.last_usage.get("output_tokens"),
                self.last_usage.get("total_tokens"),
                self.last_usage.get("estimated_cost_usd"),
            )

        return final_data, output_path
