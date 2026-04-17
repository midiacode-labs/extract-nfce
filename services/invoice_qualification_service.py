import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    from openai import OpenAI
except ImportError:  # Allows local fallback when the SDK is not installed
    OpenAI = None

from services.invoice_service import InvoiceExtractionService


class InvoiceQualificationService:
    """Qualifies extracted invoice JSON using the OpenAI SDK plus local safeguards."""

    DEFAULT_MODEL = "gpt-4.1-mini"

    def __init__(
        self,
        api_key: Optional[str] = None,
        client: Any = None,
        model: Optional[str] = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model or os.environ.get("OPENAI_MODEL", self.DEFAULT_MODEL)
        self.client = client if client is not None else self._create_client()

    def _create_client(self) -> Any:
        """Creates the OpenAI client when the SDK and API key are available."""
        if OpenAI is None:
            logging.warning(
                "OpenAI SDK is not installed. Falling back to local JSON qualification only."
            )
            return None

        if not self.api_key:
            logging.warning(
                "OPENAI_API_KEY is not configured. Falling back to local JSON qualification only."
            )
            return None

        return OpenAI(api_key=self.api_key)

    @staticmethod
    def sanitize_item_description(value: str) -> str:
        """Removes tax or OCR noise from an item description."""
        cleaned = InvoiceExtractionService.normalize_text(value)
        removal_patterns = [
            r"(?i)\btrib(?:\.?|uto(?:s)?| aprox(?:imado(?:s)?)?)\b.*$",
            r"(?i)\bvalor\s+aprox(?:imado)?\s+dos\s+tributos\b.*$",
            r"(?i)\b(?:imposto(?:s)?|icms|ipi|pis|cofins)\b.*$",
            r"(?i)\b(?:federal|estadual|municipal)\b.*$",
        ]
        for pattern in removal_patterns:
            cleaned = re.sub(pattern, "", cleaned).strip(" -|,;:")

        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -|,;:")
        return cleaned or InvoiceExtractionService.normalize_text(value)

    def apply_local_fixes(self, extracted_data: Dict[str, Any]) -> Dict[str, Any]:
        """Applies conservative local cleanup before or instead of the LLM call."""
        qualified_data = json.loads(json.dumps(extracted_data, ensure_ascii=False))

        consumer = qualified_data.setdefault("consumer", {})
        if consumer.get("state"):
            consumer["state"] = InvoiceExtractionService.normalize_text(consumer["state"]).upper()
        for field in ["street", "city", "neighborhood", "zip_code", "address", "name"]:
            if consumer.get(field):
                consumer[field] = InvoiceExtractionService.normalize_text(consumer[field])

        consumer_name = consumer.get("name")
        if consumer_name and consumer.get("street"):
            street = consumer["street"]
            if street.upper().startswith(consumer_name.upper()):
                consumer["street"] = street[len(consumer_name):].strip(" ,-;")

        InvoiceExtractionService.compose_consumer_address(consumer)

        for item in qualified_data.get("items", []):
            description = item.get("description")
            if description:
                item["description"] = self.sanitize_item_description(description)
            self.fix_item_total_price(item)

        return qualified_data

    @staticmethod
    def _parse_br_decimal(value: str) -> Optional[float]:
        """Parses a decimal string handling both US and BR formats."""
        return InvoiceExtractionService.parse_decimal(value)

    @classmethod
    def fix_item_total_price(cls, item: Dict[str, Any]) -> None:
        """Recalculates total_price when unit_price * quantity diverges from it."""
        unit_price = cls._parse_br_decimal(item.get("unit_price", ""))
        quantity = cls._parse_br_decimal(item.get("quantity", ""))
        total_price = cls._parse_br_decimal(item.get("total_price", ""))

        if unit_price is None or quantity is None:
            return

        expected = round(unit_price * quantity, 2)

        if total_price is not None and abs(total_price - expected) < 0.01:
            return

        # Format with the same decimal separator used in the original value
        original = item.get("total_price", "")
        if "," in original and "." not in original:
            item["total_price"] = f"{expected:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        else:
            item["total_price"] = f"{expected:.2f}"

    def build_messages(self, extracted_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Builds the prompt sent to the OpenAI Responses API."""
        instructions = (
            "You qualify structured JSON extracted from Brazilian NFC-e/NF-e documents. "
            "Return only a valid JSON object with the keys consumer and items. "
            "Tasks: (1) validate and correct the consumer address using zip_code as the strongest "
            "signal, reviewing street, neighborhood, city, state, and composed address; "
            "(2) sanitize each item.description so it contains only the purchased "
            "product name, removing taxes, tributes, OCR artifacts, codes, unit "
            "or pricing details when they are not part of the product name. "
            "Preserve values when uncertain and do not invent items."
        )
        payload = json.dumps(extracted_data, ensure_ascii=False, indent=2)
        return [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": instructions}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": payload}],
            },
        ]

    @staticmethod
    def extract_response_text(response: Any) -> str:
        """Extracts plain text from an OpenAI response object."""
        if hasattr(response, "output_text") and response.output_text:
            return response.output_text

        output = getattr(response, "output", None) or []
        for item in output:
            content = getattr(item, "content", None) or []
            for entry in content:
                text_value = getattr(entry, "text", None)
                if text_value:
                    return text_value

        raise RuntimeError("The OpenAI response did not contain a text payload.")

    @staticmethod
    def parse_json_response(text: str) -> Dict[str, Any]:
        """Parses a JSON object that may arrive wrapped in Markdown fences."""
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
            if not match:
                raise
            return json.loads(match.group(0))

    def merge_qualified_data(
        self,
        base_data: Dict[str, Any],
        qualified_fragment: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Merges OpenAI corrections without breaking the existing project schema."""
        merged = json.loads(json.dumps(base_data, ensure_ascii=False))

        consumer_updates = qualified_fragment.get("consumer", {})
        if isinstance(consumer_updates, dict):
            consumer = merged.setdefault("consumer", {})
            for key in [
                "name",
                "document",
                "street",
                "zip_code",
                "city",
                "neighborhood",
                "state",
                "address",
            ]:
                value = consumer_updates.get(key)
                if value:
                    normalized_value = InvoiceExtractionService.normalize_text(str(value))
                    consumer[key] = normalized_value.upper() if key == "state" else normalized_value

            InvoiceExtractionService.compose_consumer_address(consumer)
            if consumer_updates.get("address"):
                consumer["address"] = InvoiceExtractionService.normalize_text(
                    str(consumer_updates["address"])
                )

        item_updates = qualified_fragment.get("items", [])
        if isinstance(item_updates, list):
            for index, item in enumerate(merged.get("items", [])):
                ai_item = item_updates[index] if index < len(item_updates) else {}
                if isinstance(ai_item, dict) and ai_item.get("description"):
                    item["description"] = self.sanitize_item_description(ai_item["description"])
                elif item.get("description"):
                    item["description"] = self.sanitize_item_description(item["description"])

        return merged

    def qualify_data(self, extracted_data: Dict[str, Any]) -> Dict[str, Any]:
        """Qualifies extracted JSON with OpenAI and falls back to local normalization."""
        locally_qualified = self.apply_local_fixes(extracted_data)
        if self.client is None:
            return locally_qualified

        try:
            response = self.client.responses.create(
                model=self.model,
                input=self.build_messages(locally_qualified),
            )
            response_text = self.extract_response_text(response)
            qualified_fragment = self.parse_json_response(response_text)
            return self.merge_qualified_data(locally_qualified, qualified_fragment)
        except Exception as exc:
            logging.warning(
                "OpenAI qualification failed and local normalization will be used instead: %s",
                exc,
            )
            return locally_qualified

    @staticmethod
    def resolve_output_path(input_json_path: str, output: Optional[str] = None) -> str:
        """Builds the output path for the qualified JSON artifact."""
        if output:
            if not os.path.dirname(output):
                return os.path.join(os.path.dirname(input_json_path), output)
            return output

        directory = os.path.dirname(input_json_path)
        base_name = os.path.splitext(os.path.basename(input_json_path))[0]
        if base_name.endswith("_qualified"):
            return input_json_path
        return os.path.join(directory, f"{base_name}_qualified.json")

    def qualify_and_save(
        self,
        extracted_data: Dict[str, Any],
        source_output_path: str,
        output: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """Qualifies extracted JSON and saves the result to a sibling file."""
        qualified_data = self.qualify_data(extracted_data)
        qualified_output_path = self.resolve_output_path(source_output_path, output=output)
        with open(qualified_output_path, "w", encoding="utf-8") as file_handle:
            json.dump(qualified_data, file_handle, indent=4, ensure_ascii=False)
        return qualified_data, qualified_output_path

    def qualify_file(
        self,
        input_json_path: str,
        output: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """Loads a JSON file, qualifies it, and writes a new _qualified JSON file."""
        with open(input_json_path, "r", encoding="utf-8") as file_handle:
            extracted_data = json.load(file_handle)
        return self.qualify_and_save(
            extracted_data,
            source_output_path=input_json_path,
            output=output,
        )
