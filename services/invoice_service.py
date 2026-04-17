import json
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple

try:
    import cv2
except ImportError:  # Allows parsing without image preprocessing dependencies
    cv2 = None

try:
    import numpy as np
except ImportError:
    np = None

try:
    import boto3
except ImportError:  # Allows parser-only usage in minimal environments
    boto3 = None

try:
    from botocore.exceptions import ClientError
except ImportError:
    ClientError = Exception

from services.cost_estimation import build_textract_usage_summary


class InvoiceExtractionService:
    """Service layer for invoice OCR parsing and AWS Textract image processing."""

    MAX_TEXTRACT_FILE_SIZE_MB = 10.0
    PREPROCESS_MIN_DIMENSION = 1400
    PREPROCESS_MAX_DIMENSION = 2200

    def __init__(
        self,
        region: Optional[str] = None,
        enable_preprocessing: bool = True,
    ) -> None:
        self.region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        self.enable_preprocessing = enable_preprocessing
        self.last_usage: Optional[Dict[str, Any]] = None

    def get_last_usage(self) -> Optional[Dict[str, Any]]:
        """Returns the usage summary from the last Textract extraction call."""
        return self.last_usage

    @staticmethod
    def normalize_text(value: str) -> str:
        """Collapses OCR whitespace and control characters into a clean single-line string."""
        normalized = str(value).replace("\u00a0", " ")
        normalized = re.sub(r"[\r\n\t]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    @staticmethod
    def parse_decimal(value: str) -> Optional[float]:
        """Parses a decimal string from Textract handling US and BR formats.

        Detects the format by the relative position of comma and period:
        - ``4,198.0000`` → US format (comma = thousands) → 4198.0
        - ``4.198,00``   → BR format (period = thousands) → 4198.0
        - ``1,0000``     → BR format (comma = decimal)    → 1.0
        - ``29.8000``    → US format (period = decimal)   → 29.8
        """
        if not value:
            return None
        cleaned = re.sub(r"[^\d.,]", "", str(value))
        if not cleaned:
            return None

        last_comma = cleaned.rfind(",")
        last_period = cleaned.rfind(".")

        if last_comma >= 0 and last_period >= 0:
            if last_comma > last_period:
                # BR: period is thousands, comma is decimal (e.g. 4.198,00)
                cleaned = cleaned.replace(".", "").replace(",", ".")
            else:
                # US: comma is thousands, period is decimal (e.g. 4,198.0000)
                cleaned = cleaned.replace(",", "")
        elif last_comma >= 0:
            # Only comma: BR decimal (e.g. 1,0000)
            cleaned = cleaned.replace(",", ".")
        # else: only period or no separator → already fine

        try:
            return float(cleaned)
        except ValueError:
            return None

    @staticmethod
    def extract_date(text: str) -> Optional[str]:
        """Extracts the first invoice-like date from OCR text."""
        match = re.search(r"\b\d{2}/\d{2}/\d{4}\b", text)
        return match.group() if match else None

    def set_issue_date(self, identification: Dict[str, Any], value: str) -> None:
        """Stores the invoice issue date in the identification section."""
        issue_date = self.extract_date(value)
        if issue_date:
            identification["issue_date"] = issue_date

    @staticmethod
    def validate_access_key(key: str) -> bool:
        """Validates the check digit of an electronic invoice access key with 44 digits."""
        key = re.sub(r"\D", "", key)
        if len(key) != 44:
            return False

        total_sum = 0
        weight = 2
        for char in reversed(key[:-1]):
            total_sum += int(char) * weight
            weight += 1
            if weight > 9:
                weight = 2

        remainder = total_sum % 11
        check_digit = 0 if remainder <= 1 else 11 - remainder
        return str(check_digit) == key[-1]

    @staticmethod
    def extract_document_number(text: str) -> Optional[str]:
        """Extracts CPF or CNPJ values from a text snippet."""
        match = re.search(
            r"\d{3}\.\d{3}\.\d{3}-\d{2}|\b\d{11}\b|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}",
            text,
        )
        return match.group() if match else None

    @staticmethod
    def extract_cnpj(text: str) -> Optional[str]:
        """Extracts only CNPJ values from a text snippet."""
        match = re.search(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\b\d{14}\b", text)
        return match.group() if match else None

    @staticmethod
    def is_cnpj_document(value: str) -> bool:
        """Returns True when the value is a Brazilian CNPJ."""
        return len(re.sub(r"\D", "", value or "")) == 14

    def assign_detected_document(
        self,
        text: str,
        consumer_data: Dict[str, Any],
        emitter_data: Dict[str, Any],
    ) -> None:
        """Routes OCR-detected documents to the correct JSON section."""
        document = self.extract_document_number(text)
        if not document:
            return

        upper_text = self.normalize_text(text).upper()
        if "CPF" in upper_text:
            consumer_data.setdefault("document", document)
            return

        if self.is_cnpj_document(document):
            consumer_markers = ["DESTINAT", "RECEIVER", "BUYER", "CUSTOMER", "CLIENTE"]
            is_consumer_cnpj = (
                any(marker in upper_text for marker in consumer_markers)
                and "CONSUMIDOR FINAL" not in upper_text
            )
            if is_consumer_cnpj:
                consumer_data.setdefault("document", document)
            else:
                emitter_data.setdefault("cnpj", document)
            return

        consumer_data.setdefault("document", document)

    @staticmethod
    def is_date_or_time(text: str) -> bool:
        """Returns True for pure date or time OCR lines that should not become address."""
        normalized = re.sub(r"\s+", " ", text).strip()
        return bool(
            re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", normalized)
            or re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", normalized)
        )

    @staticmethod
    def is_zip_code(text: str) -> bool:
        """Returns True when the text looks like a Brazilian CEP."""
        normalized = re.sub(r"\s+", "", text)
        return bool(re.fullmatch(r"\d{5}-?\d{3}", normalized))

    @staticmethod
    def is_state_code(text: str) -> bool:
        """Returns True when the text looks like a valid Brazilian UF code."""
        normalized = re.sub(r"\s+", " ", text).strip().upper()
        valid_states = {
            "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA",
            "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN",
            "RS", "RO", "RR", "SC", "SP", "SE", "TO",
        }
        return normalized in valid_states

    def is_street_candidate(self, text: str) -> bool:
        """Returns True when the OCR line looks like a street or address line."""
        normalized = re.sub(r"\s+", " ", text).strip().upper()
        if len(normalized) < 5 or self.is_date_or_time(normalized):
            return False

        if self.is_consumer_form_label(normalized):
            return False

        if any(keyword in normalized for keyword in ["CPF", "CNPJ", "CONSUMIDOR", "DESTINAT"]):
            return False

        address_keywords = [
            "RUA", "AV ", "AV.", "AVENIDA", "ALAMEDA", "ESTRADA", "RODOVIA",
            "TRAVESSA", "TV ", "PRACA", "PRAÇA", "APTO", "BLOCO", "CASA",
            "LOTE", "QUADRA", "NUMERO", "NRO",
        ]
        return any(keyword in normalized for keyword in address_keywords)

    def is_location_candidate(self, text: str) -> bool:
        """Returns True when the OCR line looks like city or neighborhood info."""
        normalized = re.sub(r"\s+", " ", text).strip()
        if len(normalized) < 4 or re.search(r"\d", normalized) or self.is_date_or_time(normalized):
            return False

        upper_text = normalized.upper()
        if self.is_consumer_form_label(upper_text):
            return False

        blocked_keywords = [
            "CPF", "CNPJ", "CONSUMIDOR", "DESTINAT", "CALCULO", "CÁLCULO",
            "VALOR", "TOTAL", "TRIB", "INSCRICAO", "INSCRIÇÃO", "DATA",
            "CHAVE", "DANFE", "NOTA", "CIT",
        ]
        if any(keyword in upper_text for keyword in blocked_keywords):
            return False

        words = [word for word in re.split(r"\s+", normalized) if word]
        if normalized == normalized.upper() and words and all(len(word) <= 4 for word in words):
            return False

        return True

    def is_name_candidate(self, text: str) -> bool:
        """Returns True when the OCR line looks like a consumer name."""
        normalized = re.sub(r"\s+", " ", text).strip()
        parts = [part for part in normalized.split() if part]
        if len(normalized) < 10 or len(parts) < 2 or re.search(r"\d", normalized):
            return False

        upper_text = normalized.upper()
        if any(keyword in upper_text for keyword in ["CPF", "CNPJ", "CONSUMIDOR", "DESTINAT"]):
            return False

        if self.is_consumer_form_label(upper_text):
            return False

        return not self.is_street_candidate(normalized)

    def should_use_consumer_name(
        self,
        candidate: str,
        data: Dict[str, Any],
    ) -> bool:
        """Returns True when a detected name should be stored as consumer name."""
        normalized_candidate = self.normalize_text(candidate)
        if not self.is_name_candidate(normalized_candidate):
            return False

        current_name = self.normalize_text(data.get("consumer", {}).get("name", ""))
        emitter_name = self.normalize_text(data.get("emitter", {}).get("company_name", ""))

        candidate_upper = normalized_candidate.upper()
        current_upper = current_name.upper()
        emitter_upper = emitter_name.upper()

        if emitter_upper and candidate_upper == emitter_upper:
            return False

        if not current_name:
            return True

        if emitter_upper and current_upper == emitter_upper:
            return True

        return False

    @staticmethod
    def is_consumer_form_label(text: str) -> bool:
        """Returns True when the line looks like a destination form label, not a value."""
        normalized = re.sub(r"\s+", " ", text).strip().upper()
        if not normalized:
            return False

        exact_labels = {
            "NOME / RAZAO SOCIAL",
            "NOME / RAZÃO SOCIAL",
            "CPF/CNPJ",
            "CNPJ/CPF",
            "DATA DA EMISSAO",
            "DATA DA EMISSÃO",
            "HORA DA EMISSAO",
            "HORA DA EMISSÃO",
            "DATA ENTRADA/SAIDA",
            "DATA ENTRADA/SAÍDA",
            "HORA ENTRADA/SAIDA",
            "HORA ENTRADA/SAÍDA",
            "ENDERECO",
            "ENDEREÇO",
            "BAIRRO/DISTRITO",
            "CEP",
            "MUNICIPIO",
            "MUNICÍPIO",
            "UF",
            "FONE/FAX",
            "INSCRICAO ESTADUAL",
            "INSCRIÇÃO ESTADUAL",
        }
        if normalized in exact_labels:
            return True

        if "/" in normalized and any(
            marker in normalized
            for marker in [
                "NOME",
                "RAZAO SOCIAL",
                "RAZÃO SOCIAL",
                "DATA",
                "EMISSAO",
                "EMISSÃO",
                "BAIRRO",
                "DISTRITO",
                "FONE",
                "FAX",
            ]
        ):
            return True

        prefix_labels = [
            "NOME ",
            "RAZAO ",
            "RAZÃO ",
            "DATA ",
            "HORA ",
            "ENDERECO ",
            "ENDEREÇO ",
            "MUNICIPIO ",
            "MUNICÍPIO ",
            "BAIRRO ",
            "CEP ",
            "FONE ",
            "INSCRICAO ",
            "INSCRIÇÃO ",
        ]
        return any(normalized.startswith(prefix) for prefix in prefix_labels)

    @staticmethod
    def is_consumer_section_marker(text: str) -> bool:
        """Returns True when the OCR line marks the consumer/destination block."""
        normalized = re.sub(r"\s+", " ", text).strip().upper()
        if not normalized:
            return False

        if "DESTINAT" in normalized:
            return True

        consumer_markers = [
            "CONSUMIDOR",
            "CONSUMIDOR FINAL",
            "CONSUMIDOR NAO IDENTIFICADO",
            "CONSUMIDOR NÃO IDENTIFICADO",
            "IDENTIFICACAO DO CONSUMIDOR",
            "IDENTIFICAÇÃO DO CONSUMIDOR",
        ]
        return normalized in consumer_markers

    @staticmethod
    def compose_consumer_address(consumer_data: Dict[str, Any]) -> None:
        """Builds a stable full address string from extracted consumer components."""
        ordered_keys = ["street", "zip_code", "city", "neighborhood", "state"]
        parts = []
        seen = set()

        for key in ordered_keys:
            value = consumer_data.get(key)
            if value:
                cleaned = re.sub(r"\s+", " ", value).strip(" ,;-")
                if cleaned and cleaned not in seen:
                    parts.append(cleaned)
                    seen.add(cleaned)

        if parts:
            consumer_data["address"] = ", ".join(parts)

    def update_consumer_address(self, consumer_data: Dict[str, Any], value: str) -> None:
        """Stores consumer address components from OCR or summary fields."""
        cleaned = re.sub(r"\s+", " ", value).strip(" ,;-")
        if not cleaned or self.is_date_or_time(cleaned) or self.extract_document_number(cleaned):
            return

        if self.is_consumer_form_label(cleaned):
            return

        if cleaned == consumer_data.get("name"):
            return

        upper_text = cleaned.upper()
        neighborhood_keywords = ["BAIRRO", "JARDIM", "JD", "VILA", "PARQUE", "CENTRO"]
        has_address_anchor = any(key in consumer_data for key in ["street", "zip_code", "state"])

        if self.is_zip_code(cleaned):
            consumer_data["zip_code"] = cleaned
        elif self.is_state_code(cleaned):
            consumer_data["state"] = upper_text
        elif self.is_street_candidate(cleaned):
            consumer_data.setdefault("street", cleaned)
        elif has_address_anchor and any(keyword in upper_text for keyword in neighborhood_keywords):
            consumer_data.setdefault("neighborhood", cleaned)
        elif has_address_anchor and self.is_location_candidate(cleaned):
            if "city" not in consumer_data:
                consumer_data["city"] = cleaned
            else:
                consumer_data.setdefault("neighborhood", cleaned)

        self.compose_consumer_address(consumer_data)

    # ------------------------------------------------------------------
    # Extraction helpers – label-based routing and block scanning
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_protocol(text: str, identification: Dict[str, Any]) -> None:
        """Extracts authorization protocol number and datetime from text."""
        match = re.search(
            r"(\d{15,})\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2})", text
        )
        if match:
            identification["authorization_protocol"] = match.group(1)
            identification["authorization_datetime"] = (
                f"{match.group(2)} {match.group(3)}"
            )
            return

        match = re.search(r"\d{15,}", text)
        if match:
            identification.setdefault("authorization_protocol", match.group())

    def _classify_labeled_field(
        self,
        label: str,
        value: str,
        data: Dict[str, Any],
    ) -> None:
        """Routes OTHER SummaryFields to the correct section based on their label."""
        ul = label.upper()

        # Emitter
        if any(kw in ul for kw in ["INSCRI", "INS. EST", "INSC EST"]):
            data["emitter"].setdefault("state_registration", value)

        # Identification
        elif any(kw in ul for kw in ["NATUREZA", "NATURE"]):
            data["identification"]["nature_of_operation"] = value
        elif "PROTOCOLO" in ul:
            self._extract_protocol(value, data["identification"])
        elif "SÉRIE" in ul or "SERIE" in ul:
            digits = re.sub(r"\D", "", value)
            if digits:
                data["identification"].setdefault("series", digits)
        elif any(kw in ul for kw in ["NF-E N", "NFC-E N"]):
            digits = re.sub(r"\D", "", value)
            if digits:
                data["identification"].setdefault("number", digits)
        elif any(kw in ul for kw in ["PÁGINA", "PAGINA", "PÁG"]):
            data["identification"]["page"] = value
        elif "DATA" in ul and "EMISS" in ul:
            self.set_issue_date(data["identification"], value)
        elif "HORA" in ul and any(kw in ul for kw in ["SAÍDA", "SAIDA", "EMISS"]):
            time_match = re.search(r"\d{1,2}:\d{2}(?::\d{2})?", value)
            if time_match:
                data["identification"]["issue_time"] = time_match.group()
        elif "DATA" in ul and any(kw in ul for kw in ["ENT", "SAÍDA", "SAIDA"]):
            date = self.extract_date(value)
            if date:
                data["identification"]["entry_exit_date"] = date

        # Tax calculation (check ICMS ST before ICMS)
        elif "BC" in ul and "ICMS" in ul and "ST" in ul:
            data["tax_calculation"]["icms_st_basis"] = value
        elif ("VALOR" in ul or "V." in ul) and "ICMS" in ul and "ST" in ul:
            data["tax_calculation"]["icms_st_value"] = value
        elif "BC" in ul and "ICMS" in ul:
            data["tax_calculation"]["icms_basis"] = value
        elif ("VALOR" in ul or "V." in ul) and "ICMS" in ul:
            data["tax_calculation"]["icms_value"] = value
        elif "IPI" in ul and ("VALOR" in ul or "V." in ul):
            data["tax_calculation"]["ipi_value"] = value
        elif "TOTAL" in ul and "PRODUTO" in ul:
            data["tax_calculation"]["total_products_value"] = value
        elif "FRETE" in ul and "POR CONTA" not in ul:
            data["tax_calculation"]["freight_value"] = value
        elif "SEGURO" in ul:
            data["tax_calculation"]["insurance_value"] = value
        elif "DESCONTO" in ul:
            data["tax_calculation"]["discount"] = value
        elif "OUTRAS" in ul and "DESPESA" in ul:
            data["tax_calculation"]["other_expenses"] = value
        elif "APROX" in ul and "TRIB" in ul:
            data["tax_calculation"]["approximate_tax"] = value
            data["fiscal_message"]["approximate_tax"] = value
        elif "TOTAL" in ul and "NOTA" in ul:
            data["tax_calculation"]["total_invoice_value"] = value
            data["totals"].setdefault("total_invoice_value", value)

        # Consumer
        elif any(kw in ul for kw in ["CPF/CNPJ", "CNPJ/CPF", "CPF DEST", "CNPJ DEST"]):
            doc = self.extract_document_number(value)
            if doc:
                data["consumer"]["document"] = doc
        elif any(kw in ul for kw in ["DESTINAT", "NOME DO DEST"]):
            data["consumer"]["name"] = value
        elif any(kw in ul for kw in ["CEP", "CÓDIGO POSTAL", "CODIGO POSTAL"]):
            data["consumer"]["zip_code"] = value.strip()
            self.compose_consumer_address(data["consumer"])
        elif "BAIRRO" in ul or "DISTRITO" in ul:
            data["consumer"]["neighborhood"] = value.strip()
            self.compose_consumer_address(data["consumer"])
        elif any(kw in ul for kw in ["MUNICÍPIO", "MUNICIPIO", "CIDADE"]):
            data["consumer"]["city"] = value.strip()
            self.compose_consumer_address(data["consumer"])
        elif "UF" in ul and len(ul) <= 15:
            state = value.strip().upper()
            if self.is_state_code(state):
                data["consumer"]["state"] = state
                self.compose_consumer_address(data["consumer"])
        elif "FONE" in ul or "TELEFONE" in ul:
            data["consumer"]["phone"] = value
        elif any(kw in ul for kw in ["ENDEREÇO", "ENDERECO"]) and "EMITENTE" not in ul:
            self.update_consumer_address(data["consumer"], value)

        # Transport
        elif any(kw in ul for kw in ["FRETE POR CONTA", "FRETE POR"]):
            data["transport"]["freight_type"] = value
        elif any(kw in ul for kw in ["CÓDIGO ANTT", "CODIGO ANTT"]):
            data["transport"]["antt_code"] = value
        elif "PLACA" in ul:
            data["transport"]["vehicle_plate"] = value

        # Totals / Payments
        elif "FORMA" in ul and "PAGAMENTO" in ul:
            data["totals"].setdefault("payments", []).append({"method": value})
        elif "VALOR PAGO" in ul:
            payments = data["totals"].get("payments", [])
            if payments:
                payments[-1]["amount"] = value
            else:
                data["totals"].setdefault("payments", [{"amount": value}])
        elif "TROCO" in ul:
            data["totals"]["change"] = value

        # Additional info
        elif any(
            kw in ul
            for kw in ["INFORMAÇÕES COMPLEMENTARES", "INFORMACOES COMPLEMENTARES"]
        ):
            data["additional_info"]["complementary_info"] = value
        elif "RESERVADO" in ul and "FISCO" in ul:
            data["additional_info"]["fiscal_notes"] = value

        # Fallback
        else:
            self._fallback_classify(value, data)

    def _fallback_classify(self, value: str, data: Dict[str, Any]) -> None:
        """Fallback routing for OTHER fields without identifiable labels."""
        document = self.extract_document_number(value)
        if document:
            self.assign_detected_document(value, data["consumer"], data["emitter"])
        elif self.extract_date(value) and "issue_date" not in data["identification"]:
            self.set_issue_date(data["identification"], value)

    def _extract_item(self, line_item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extracts a single item from a Textract LineItem."""
        item: Dict[str, Any] = {}

        for expense_field in line_item.get("LineItemExpenseFields", []):
            if "Type" not in expense_field or "ValueDetection" not in expense_field:
                continue

            item_type = self.normalize_text(expense_field["Type"]["Text"])
            item_val = self.normalize_text(expense_field["ValueDetection"]["Text"])

            if item_type == "ITEM":
                item["description"] = item_val
            elif item_type == "PRICE":
                item["total_price"] = item_val
            elif item_type == "QUANTITY":
                item["quantity"] = item_val
            elif item_type == "UNIT_PRICE":
                item["unit_price"] = item_val
            elif item_type == "PRODUCT_CODE":
                item["code"] = item_val
            elif item_type == "EXPENSE_ROW":
                item["expense_row"] = item_val
            else:
                key = item_type.lower().replace(" ", "_")
                item[key] = item_val

        if item:
            self._refine_description_from_expense_row(item)

        return item if item else None

    @staticmethod
    def _refine_description_from_expense_row(item: Dict[str, Any]) -> None:
        """Recovers a more complete description from the expense_row when Textract
        truncated the ITEM field."""
        expense_row = item.get("expense_row", "")
        description = item.get("description", "")
        if not expense_row or not description:
            return

        desc_pos = expense_row.find(description)
        if desc_pos < 0:
            return

        anchor_values: set[str] = set()
        for key in (
            "code",
            "ncm",
            "cst",
            "cfop",
            "unit",
            "quantity",
            "unit_price",
            "total_price",
        ):
            val = item.get(key, "")
            if val and val.strip():
                anchor_values.add(val.strip())

        after = expense_row[desc_pos + len(description):]
        remaining_tokens = after.split()

        if not any(token in anchor_values for token in remaining_tokens):
            return

        extra_words: list[str] = []
        for token in remaining_tokens:
            if token in anchor_values:
                break
            extra_words.append(token)

        if extra_words:
            item["description"] = description + " " + " ".join(extra_words)

    @staticmethod
    def _is_items_section_marker(text: str) -> bool:
        """Returns True when the OCR line marks the items/services table."""
        normalized = re.sub(r"\s+", " ", text).strip().upper()
        return "DADOS DO PRODUTO" in normalized or "DADOS DOS PRODUTO" in normalized

    @staticmethod
    def _is_item_table_header_line(text: str) -> bool:
        """Returns True for the column header row inside the items table."""
        normalized = re.sub(r"\s+", " ", text).strip().upper()
        header_markers = [
            "COD. PROD",
            "DESCRICAO DOS PRODUTOS",
            "DESCRIÇÃO DOS PRODUTOS",
            "NCM/SH",
            "CFOP",
            "UNIDADE",
            "QTDE",
            "V. UNITARIO",
            "V. UNITÁRIO",
            "V. TOTAL",
        ]
        return any(marker in normalized for marker in header_markers)

    @staticmethod
    def _is_item_start_line(text: str) -> bool:
        """Returns True when the OCR line looks like the beginning of an item row."""
        normalized = re.sub(r"\s+", " ", text).strip().upper()
        if not normalized:
            return False

        match = re.match(r"^(\d{4,})\s+(.+)$", normalized)
        if not match:
            return False

        remainder = match.group(2).strip()
        if not remainder or remainder[0].isdigit():
            return False

        return bool(re.search(r"[A-Z]", remainder))

    def _parse_item_line(self, text: str) -> Optional[Dict[str, Any]]:
        """Parses a product row from raw OCR text when Textract misses line items."""
        normalized = self.normalize_text(text)
        if not normalized or self._is_item_table_header_line(normalized):
            return None

        full_pattern = re.compile(
            r"^(?P<code>\d{4,})\s+"
            r"(?P<description>.+?)\s+"
            r"(?P<ncm>\d{4,10})\s+"
            r"(?P<cst>\d{2,3})\s+"
            r"(?P<cfop>\d{3,4})\s+"
            r"(?P<unit>[A-Z]{1,5})\s+"
            r"(?P<quantity>\d+[.,]\d+)\s+"
            r"(?P<unit_price>\d[\d.,]*)\s+"
            r"(?P<total_price>\d[\d.,]*)"
        )
        compact_pattern = re.compile(
            r"^(?P<code>\d{4,})\s+"
            r"(?P<description>.+?)\s+"
            r"(?P<unit>[A-Z]{1,5})\s+"
            r"(?P<quantity>\d+[.,]\d+)\s+"
            r"(?P<unit_price>\d[\d.,]*)\s+"
            r"(?P<total_price>\d[\d.,]*)"
        )

        for pattern in (full_pattern, compact_pattern):
            match = pattern.search(normalized)
            if not match:
                continue

            item = {
                "code": match.group("code"),
                "description": match.group("description").strip(" -|,;:"),
                "unit": match.group("unit"),
                "quantity": match.group("quantity"),
                "unit_price": match.group("unit_price"),
                "total_price": match.group("total_price"),
                "expense_row": normalized,
            }
            if "ncm" in match.groupdict() and match.groupdict().get("ncm"):
                item["ncm"] = match.group("ncm")
            if "cst" in match.groupdict() and match.groupdict().get("cst"):
                item["cst"] = match.group("cst")
            if "cfop" in match.groupdict() and match.groupdict().get("cfop"):
                item["cfop"] = match.group("cfop")
            return item

        return None

    def _extract_items_from_blocks(self, blocks: list) -> list[Dict[str, Any]]:
        """Builds items from OCR lines when AnalyzeExpense misses the item table."""
        items: list[Dict[str, Any]] = []
        inside_items_section = False
        current_item_lines: list[str] = []

        def flush_current_item() -> None:
            if not current_item_lines:
                return

            combined_text = self.normalize_text(" ".join(current_item_lines))
            parsed_item = self._parse_item_line(combined_text)
            if parsed_item:
                items.append(parsed_item)
            current_item_lines.clear()

        for block in blocks:
            if block.get("BlockType") != "LINE":
                continue

            text = self.normalize_text(block.get("Text", ""))
            upper_text = text.upper()

            if self._is_items_section_marker(upper_text):
                inside_items_section = True
                continue

            if not inside_items_section:
                continue

            if any(
                marker in upper_text
                for marker in ["DADOS ADICIONAIS", "RESERVADO AO FISCO", "RESERVAD"]
            ):
                flush_current_item()
                break

            if self._is_item_table_header_line(upper_text):
                continue

            if self._is_item_start_line(text):
                flush_current_item()
                current_item_lines.append(text)
                continue

            if current_item_lines:
                current_item_lines.append(text)
                continue

            if items and any(
                keyword in upper_text
                for keyword in ["TRIB", "IMEI", "FEDERAL", "ESTADUAL"]
            ):
                items[-1]["expense_row"] = f"{items[-1].get('expense_row', '')} {text}".strip()

        flush_current_item()
        return items

    # ------------------------------------------------------------------
    # Geometry-based cell extraction (fragmented LINE blocks per cell)
    # ------------------------------------------------------------------

    _HEADER_CELL_PATTERN = re.compile(
        r"DESCRI|UNITARI|UNITÁRIO|CFOP|QTDE|NCM|MCM"
        r"|V\.\s*TOTAL|V\.\s*ICMS|BC\s*ICM|UNIDADE"
        r"|PRODUTO|SERVI|COD\s*\.?\s*PROD|C[OÓ]N\s*PROD"
        r"|COE\s*PROB|ALIQ|\bIPI\b|\bALIO\b|\bALK\b"
        r"|\bOTDE\b|\bCIT\b|\bCROT\b|\bCROR\b",
        re.IGNORECASE,
    )

    _INFO_CELL_KEYWORDS = ("TRIB", "FEDERAL", "ESTADUAL", "IMEI")

    @staticmethod
    def _is_data_cell_content(text: str) -> bool:
        """Returns True when the cell text is clearly numeric item data."""
        stripped = text.strip()
        if re.match(r"^\d[\d.,]*$", stripped):
            return True
        # Short alpha tokens (UN, KG) are ambiguous with header noise;
        # only accept 2-letter unit codes that look like real measurement units.
        if re.match(r"^(?:UN|KG|CX|PC|LT|MT|M2|ML)$", stripped, re.IGNORECASE):
            return True
        return False

    def _identify_item_from_cells(
        self, cells: list[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Reconstructs a single item dict from a group of fragmented cell blocks."""
        code: Optional[str] = None
        description_parts: list[str] = []
        ncm: Optional[str] = None
        cst: Optional[str] = None
        cfop: Optional[str] = None
        unit: Optional[str] = None
        decimals: list[Tuple[float, str]] = []

        for cell in sorted(cells, key=lambda c: c["left"]):
            text = cell["text"].strip()
            left = cell["left"]
            if not text:
                continue

            stripped_digits = re.sub(r"\s", "", text)

            # Decimal number (e.g. 1.0000, 999,00, 1:0000 for OCR errors)
            cleaned = re.sub(r"[:;]", ",", text)
            if re.match(r"^\d[\d.,]+$", cleaned) and ("." in cleaned or "," in cleaned):
                decimals.append((left, cleaned))
                continue

            # Pure integer
            if stripped_digits.isdigit():
                n = len(stripped_digits)
                if n == 8 and ncm is None:
                    ncm = stripped_digits
                elif 4 <= n <= 9 and code is None and left < 0.15:
                    code = stripped_digits
                elif 4 <= n <= 5 and cfop is None and ncm is not None:
                    cfop = stripped_digits
                elif 2 <= n <= 3 and cst is None and ncm is not None:
                    cst = stripped_digits
                continue

            # Unit abbreviation (UN, KG, CX, PC, etc.)
            if re.match(r"^(?:UN|KG|CX|PC|LT|MT|M2|ML|PÇ|PR)$", text, re.IGNORECASE):
                unit = text.upper()
                continue

            # Description: text with letters, length > 3
            if re.search(r"[A-Za-z]", text) and len(text) > 3:
                description_parts.append(text)

        description = " ".join(description_parts).strip() if description_parts else None

        if not code and not description:
            return None

        item: Dict[str, Any] = {}
        if code:
            item["code"] = code
        if description:
            item["description"] = description
        if ncm:
            item["ncm"] = ncm
        if cst:
            item["cst"] = cst
        if cfop:
            item["cfop"] = cfop
        if unit:
            item["unit"] = unit

        # Assign decimals left-to-right: quantity, unit_price, total_price
        decimals.sort(key=lambda d: d[0])
        if len(decimals) >= 3:
            item["quantity"] = decimals[0][1]
            item["unit_price"] = decimals[1][1]
            item["total_price"] = decimals[2][1]
        elif len(decimals) == 2:
            item["unit_price"] = decimals[0][1]
            item["total_price"] = decimals[1][1]
        elif len(decimals) == 1:
            item["total_price"] = decimals[0][1]

        item["expense_row"] = self.normalize_text(
            " ".join(c["text"] for c in sorted(cells, key=lambda c: c["left"]))
        )
        return item

    def _extract_items_from_cell_blocks(
        self, blocks: list
    ) -> list[Dict[str, Any]]:
        """Reconstructs items from fragmented per-cell LINE blocks using geometry."""
        inside_items = False
        cells: list[Dict[str, Any]] = []

        for block in blocks:
            if block.get("BlockType") != "LINE":
                continue

            text = self.normalize_text(block.get("Text", ""))
            upper = text.upper()

            if self._is_items_section_marker(upper):
                inside_items = True
                continue
            if not inside_items:
                continue
            if any(m in upper for m in ["DADOS ADICIONAIS", "RESERVADO AO FISCO", "RESERVAD"]):
                break

            geo = block.get("Geometry", {}).get("BoundingBox", {})
            top = geo.get("Top", 0)
            left = geo.get("Left", 0)
            if top == 0 and left == 0:
                continue
            cells.append({"text": text, "top": top, "left": left})

        if not cells:
            return []

        # Filter header cells using keyword pattern + Y-band exclusion.
        # Cells with data-like content (numbers, decimals, unit codes)
        # are preserved even when they fall inside the header Y-band.
        confirmed_header_tops = [
            c["top"] for c in cells if self._HEADER_CELL_PATTERN.search(c["text"])
        ]

        if confirmed_header_tops:
            h_min = min(confirmed_header_tops) - 0.003
            h_max = max(confirmed_header_tops) + 0.003
            data_cells = [
                c for c in cells
                if (
                    not (h_min <= c["top"] <= h_max)
                    or self._is_data_cell_content(c["text"])
                )
                and not self._HEADER_CELL_PATTERN.search(c["text"])
            ]
        else:
            data_cells = [
                c for c in cells
                if not self._HEADER_CELL_PATTERN.search(c["text"])
            ]

        # Separate tax/info cells (keep their Y for per-item assignment)
        info_cells: list[Dict[str, Any]] = []
        clean_cells: list[Dict[str, Any]] = []
        for c in data_cells:
            if any(kw in c["text"].upper() for kw in self._INFO_CELL_KEYWORDS):
                info_cells.append(c)
            else:
                clean_cells.append(c)

        if not clean_cells:
            return []

        # Find product code cells (standalone digits at leftmost column)
        code_cells = [
            c for c in clean_cells
            if re.match(r"^\d{4,9}$", c["text"].strip()) and c["left"] < 0.15
        ]

        items: list[Dict[str, Any]] = []

        if not code_cells:
            item = self._identify_item_from_cells(clean_cells)
            if item:
                if info_cells:
                    item["expense_row"] = (
                        f"{item.get('expense_row', '')} "
                        f"{' '.join(c['text'] for c in info_cells)}"
                    ).strip()
                items.append(item)
            return items

        code_cells.sort(key=lambda c: c["top"])

        # Partition all non-code data cells into per-item groups.
        # Between consecutive code cells, find the largest Y-gap among
        # the interleaved data cells and split there.
        all_non_code = [c for c in clean_cells + info_cells if c not in code_cells]
        all_non_code.sort(key=lambda c: c["top"])

        boundaries = self._compute_item_boundaries(code_cells, all_non_code)

        for idx, cc in enumerate(code_cells):
            y_min, y_max = boundaries[idx]

            item_cells = [c for c in clean_cells if y_min <= c["top"] <= y_max]
            item = self._identify_item_from_cells(item_cells)
            if item:
                item_info = [c for c in info_cells if y_min <= c["top"] <= y_max + 0.01]
                if item_info:
                    item["expense_row"] = (
                        f"{item.get('expense_row', '')} "
                        f"{' '.join(c['text'] for c in item_info)}"
                    ).strip()
                items.append(item)

        return items

    @staticmethod
    def _compute_item_boundaries(
        code_cells: list[Dict[str, Any]],
        non_code_cells: list[Dict[str, Any]],
    ) -> list[Tuple[float, float]]:
        """Computes (y_min, y_max) for each code cell using gap analysis."""
        boundaries: list[Tuple[float, float]] = []

        for idx, cc in enumerate(code_cells):
            # Default generous bounds
            y_min = cc["top"] - 0.025
            y_max = cc["top"] + 0.03

            if idx > 0:
                prev_code = code_cells[idx - 1]
                # Find the largest Y-gap between consecutive cells in
                # the range between two code cells to split items there.
                between = sorted(
                    [c for c in non_code_cells if prev_code["top"] < c["top"] < cc["top"]],
                    key=lambda c: c["top"],
                )
                if len(between) >= 2:
                    max_gap = 0
                    split_y = (prev_code["top"] + cc["top"]) / 2
                    for i in range(1, len(between)):
                        gap = between[i]["top"] - between[i - 1]["top"]
                        if gap > max_gap:
                            max_gap = gap
                            split_y = (between[i - 1]["top"] + between[i]["top"]) / 2
                    y_min = split_y
                else:
                    y_min = (prev_code["top"] + cc["top"]) / 2

            if idx + 1 < len(code_cells):
                next_code = code_cells[idx + 1]
                between = sorted(
                    [c for c in non_code_cells if cc["top"] < c["top"] < next_code["top"]],
                    key=lambda c: c["top"],
                )
                if len(between) >= 2:
                    max_gap = 0
                    split_y = (cc["top"] + next_code["top"]) / 2
                    for i in range(1, len(between)):
                        gap = between[i]["top"] - between[i - 1]["top"]
                        if gap > max_gap:
                            max_gap = gap
                            split_y = (between[i - 1]["top"] + between[i]["top"]) / 2
                    y_max = split_y
                else:
                    y_max = (cc["top"] + next_code["top"]) / 2

            boundaries.append((y_min, y_max))

        return boundaries

    def _extract_from_blocks(
        self, blocks: list, data: Dict[str, Any]
    ) -> None:
        """Scans OCR blocks for structured data not captured by SummaryFields."""
        consumer_lines_remaining = 0
        additional_info_lines = 0
        prev_upper = ""

        for block in blocks:
            if block.get("BlockType") != "LINE":
                continue

            text = self.normalize_text(block.get("Text", ""))
            upper_text = text.upper()
            numbers = re.sub(r"\s+", "", text)

            # Access key (44 digits)
            if len(numbers) == 44 and numbers.isdigit():
                data["access_key"]["key"] = numbers
                data["access_key"]["is_valid"] = self.validate_access_key(numbers)

            # CNPJ for emitter
            cnpj = self.extract_cnpj(text)
            if cnpj and "cnpj" not in data["emitter"]:
                data["emitter"]["cnpj"] = cnpj

            # Invoice number
            nf_match = re.search(r"N[°º.]?\s*[:.]?\s*(\d{4,})", text)
            if nf_match and "number" not in data["identification"]:
                data["identification"]["number"] = nf_match.group(1)

            # Series
            serie_match = re.search(r"S[ÉE]RIE\s*[:.]?\s*(\d+)", upper_text)
            if serie_match and "series" not in data["identification"]:
                data["identification"]["series"] = serie_match.group(1)

            # Page
            page_match = re.search(
                r"P[ÁA]G(?:INA)?\s*[:.]?\s*(\d+/\d+|\d+)", upper_text
            )
            if page_match and "page" not in data["identification"]:
                data["identification"]["page"] = page_match.group(1)

            # Protocol
            if "PROTOCOLO" in prev_upper or "PROTOCOLO" in upper_text:
                self._extract_protocol(text, data["identification"])

            # Nature of operation (text on the line following the label)
            if "NATUREZA" in prev_upper and "OPERA" in prev_upper:
                if "nature_of_operation" not in data["identification"]:
                    data["identification"]["nature_of_operation"] = text

            # State registration
            ie_match = re.search(
                r"(?:INSCRI[ÇC][ÃA]O\s+ESTADUAL|INS\.?\s*EST)\s*[:\s]*(\d[\d./\s-]+\d)",
                text,
                re.IGNORECASE,
            )
            if ie_match:
                data["emitter"].setdefault(
                    "state_registration", re.sub(r"\s+", "", ie_match.group(1))
                )

            # Issue date from blocks
            if "issue_date" not in data["identification"] and self.extract_date(text):
                self.set_issue_date(data["identification"], text)

            # Transport freight type
            if re.search(r"\b(?:SEM\s+FRETE|CIF|FOB)\b", upper_text):
                data["transport"].setdefault("freight_type", text)

            # Section markers
            if self.is_consumer_section_marker(upper_text):
                consumer_lines_remaining = 15
                prev_upper = upper_text
                continue

            if "DADOS ADICIONAIS" in upper_text:
                additional_info_lines = 10
                prev_upper = upper_text
                continue

            if re.search(
                r"INFORMA[ÇC][ÕO]ES\s+COMPLEMENTARES", upper_text
            ):
                additional_info_lines = 10
                prev_upper = upper_text
                continue

            # Process consumer section blocks
            if consumer_lines_remaining > 0:
                document = self.extract_document_number(text)
                if document:
                    self.assign_detected_document(
                        text, data["consumer"], data["emitter"]
                    )
                elif self.should_use_consumer_name(text, data):
                    data["consumer"]["name"] = text.strip()
                else:
                    self.update_consumer_address(data["consumer"], text)
                consumer_lines_remaining -= 1

            # Process additional info blocks
            elif additional_info_lines > 0:
                existing = data["additional_info"].get("complementary_info", "")
                if existing:
                    data["additional_info"]["complementary_info"] = (
                        f"{existing} {text}"
                    )
                else:
                    data["additional_info"]["complementary_info"] = text
                additional_info_lines -= 1

            prev_upper = upper_text

    # ------------------------------------------------------------------
    # Main extraction entry point
    # ------------------------------------------------------------------

    def parse_expense_data(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Maps an Analyze Expense response into the DANFE/NFC-e hierarchy."""
        data: Dict[str, Any] = {
            "emitter": {},
            "identification": {},
            "consumer": {},
            "items": [],
            "totals": {},
            "access_key": {},
            "tax_calculation": {},
            "transport": {},
            "fiscal_message": {},
            "additional_info": {},
        }

        for doc in response.get("ExpenseDocuments", []):
            for field in doc.get("SummaryFields", []):
                if "Type" not in field or "ValueDetection" not in field:
                    continue

                type_name = self.normalize_text(field["Type"]["Text"])
                value = self.normalize_text(field["ValueDetection"]["Text"])
                label = self.normalize_text(
                    field.get("LabelDetection", {}).get("Text", "")
                )

                if type_name == "VENDOR_NAME":
                    data["emitter"]["company_name"] = value
                elif type_name == "VENDOR_VAT_NUMBER":
                    data["emitter"]["cnpj"] = self.extract_cnpj(value) or value
                elif type_name == "VENDOR_ADDRESS":
                    data["emitter"].setdefault("address", value)
                    if "cnpj" not in data["emitter"]:
                        cnpj = self.extract_cnpj(value)
                        if cnpj:
                            data["emitter"]["cnpj"] = cnpj
                elif type_name in ("INVOICE_RECEIPT_DATE", "INVOICE_DATE"):
                    self.set_issue_date(data["identification"], value)
                elif type_name == "TOTAL":
                    data["totals"]["total_invoice_value"] = value
                elif type_name in ("CUSTOMER_NAME", "RECEIVER_NAME", "NAME"):
                    if self.should_use_consumer_name(value, data):
                        data["consumer"]["name"] = value
                elif type_name == "RECEIVER_VAT_NUMBER":
                    data["consumer"]["document"] = value
                elif type_name in (
                    "RECEIVER_ADDRESS",
                    "CUSTOMER_ADDRESS",
                    "BUYER_ADDRESS",
                ):
                    self.update_consumer_address(data["consumer"], value)
                elif any(
                    kw in type_name.upper() for kw in ["ZIP", "POSTAL", "CEP"]
                ):
                    data["consumer"]["zip_code"] = value.strip()
                    self.compose_consumer_address(data["consumer"])
                elif any(
                    kw in type_name.upper() for kw in ["CITY", "MUNICIPALITY"]
                ):
                    data["consumer"]["city"] = value.strip()
                    self.compose_consumer_address(data["consumer"])
                elif any(
                    kw in type_name.upper()
                    for kw in ["NEIGHBOR", "DISTRICT", "BAIRRO"]
                ):
                    data["consumer"]["neighborhood"] = value.strip()
                    self.compose_consumer_address(data["consumer"])
                elif any(
                    kw in type_name.upper()
                    for kw in ["STATE", "PROVINCE", "UF"]
                ):
                    data["consumer"]["state"] = value.strip().upper()
                    self.compose_consumer_address(data["consumer"])
                elif (
                    "ADDRESS" in type_name.upper()
                    and not type_name.upper().startswith("VENDOR")
                ):
                    self.update_consumer_address(data["consumer"], value)
                elif type_name == "PAYMENT_TERMS":
                    data["totals"].setdefault("payments", []).append(
                        {"method": value}
                    )
                elif any(
                    kw in type_name.upper()
                    for kw in [
                        "CPF",
                        "CONSUMIDOR",
                        "CONSUMER",
                        "DESTINATARIO",
                        "DESTINATÁRIO",
                    ]
                ):
                    data["consumer"]["document"] = value
                elif (
                    any(
                        kw in value.upper()
                        for kw in ["CPF", "CNPJ", "DESTINATARIO", "DESTINATÁRIO"]
                    )
                    and type_name in ("OTHER", "CUSTOMER_NUMBER")
                ):
                    self.assign_detected_document(
                        value, data["consumer"], data["emitter"]
                    )
                elif type_name == "OTHER":
                    if label:
                        self._classify_labeled_field(label, value, data)
                    else:
                        self._fallback_classify(value, data)

            for group in doc.get("LineItemGroups", []):
                for line_item in group.get("LineItems", []):
                    item = self._extract_item(line_item)
                    if item:
                        data["items"].append(item)

            if not data["items"]:
                data["items"] = self._extract_items_from_blocks(doc.get("Blocks", []))

            if not data["items"]:
                data["items"] = self._extract_items_from_cell_blocks(doc.get("Blocks", []))

            self._extract_from_blocks(doc.get("Blocks", []), data)

        return data

    def resolve_output_path(self, input_file: str, output: Optional[str] = None) -> str:
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

    def validate_file_size(self, input_file: str) -> None:
        """Raises an error when the image exceeds the Textract sync limit."""
        file_size_mb = os.path.getsize(input_file) / (1024 * 1024)
        if file_size_mb > self.MAX_TEXTRACT_FILE_SIZE_MB:
            raise RuntimeError(
                "File '%s' exceeds the 10MB limit supported by AWS Textract "
                "(current size: %.2f MB)." % (input_file, file_size_mb)
            )

    def validate_document_bytes_size(self, document_bytes: bytes, source: str) -> None:
        """Raises an error when the processed image exceeds the Textract sync limit."""
        file_size_mb = len(document_bytes) / (1024 * 1024)
        if file_size_mb > self.MAX_TEXTRACT_FILE_SIZE_MB:
            raise RuntimeError(
                "Processed image derived from '%s' exceeds the 10MB limit supported by "
                "AWS Textract (current size: %.2f MB)." % (source, file_size_mb)
            )

    @staticmethod
    def _resize_for_ocr(image: Any) -> Any:
        """Normalizes resolution to a range that improves OCR stability."""
        height, width = image.shape[:2]
        min_dimension = min(height, width)
        max_dimension = max(height, width)

        scale = 1.0
        if min_dimension < InvoiceExtractionService.PREPROCESS_MIN_DIMENSION:
            scale = InvoiceExtractionService.PREPROCESS_MIN_DIMENSION / float(min_dimension)
        elif max_dimension > InvoiceExtractionService.PREPROCESS_MAX_DIMENSION:
            scale = InvoiceExtractionService.PREPROCESS_MAX_DIMENSION / float(max_dimension)

        if abs(scale - 1.0) < 0.01:
            return image

        resized = cv2.resize(
            image,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_CUBIC if scale > 1.0 else cv2.INTER_AREA,
        )
        return resized

    @staticmethod
    def _deskew(binary_image: Any) -> Any:
        """Corrects small rotation angles commonly present in mobile captures."""
        if np is None or cv2 is None:
            return binary_image

        coordinates = np.column_stack(np.where(binary_image < 200))
        if len(coordinates) < 100:
            return binary_image

        angle = cv2.minAreaRect(coordinates)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle

        if abs(angle) < 0.3 or abs(angle) > 15:
            return binary_image

        height, width = binary_image.shape[:2]
        center = (width // 2, height // 2)
        matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
        return cv2.warpAffine(
            binary_image,
            matrix,
            (width, height),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=255,
        )

    def preprocess_image_bytes(self, image_bytes: bytes) -> bytes:
        """Applies OCR-oriented image cleanup before sending bytes to Textract."""
        if not self.enable_preprocessing:
            return image_bytes

        if cv2 is None or np is None:
            logging.warning(
                "OpenCV/Numpy are unavailable. Sending original image bytes to Textract."
            )
            return image_bytes

        image_array = np.frombuffer(image_bytes, dtype=np.uint8)
        image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        if image is None:
            logging.warning("Image preprocessing skipped because decoding failed.")
            return image_bytes

        resized = self._resize_for_ocr(image)
        grayscale = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        denoised = cv2.fastNlMeansDenoising(grayscale, None, 12, 7, 21)
        contrast = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(denoised)
        blurred = cv2.GaussianBlur(contrast, (0, 0), 1.1)
        sharpened = cv2.addWeighted(contrast, 1.35, blurred, -0.35, 0)
        thresholded = cv2.adaptiveThreshold(
            sharpened,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            15,
        )
        cleaned = cv2.morphologyEx(
            thresholded,
            cv2.MORPH_OPEN,
            np.ones((2, 2), dtype=np.uint8),
        )
        deskewed = self._deskew(cleaned)

        success, encoded = cv2.imencode(".png", deskewed)
        if not success:
            logging.warning("Image preprocessing skipped because encoding failed.")
            return image_bytes

        processed_bytes = encoded.tobytes()
        logging.info(
            "Image preprocessing applied: %d bytes -> %d bytes.",
            len(image_bytes),
            len(processed_bytes),
        )
        return processed_bytes

    def create_textract_client(self):
        """Creates the AWS Textract client using the configured region."""
        if boto3 is None:
            raise RuntimeError(
                "boto3 is not installed in the active environment. "
                "Install the project requirements before running the CLI."
            )

        try:
            client = boto3.client("textract", region_name=self.region)
            logging.info("AWS Textract client initialized with region %s.", self.region)
            return client
        except Exception as exc:
            raise RuntimeError(f"Error initializing AWS client: {exc}") from exc

    def analyze_image(self, input_file: str) -> Dict[str, Any]:
        """Reads an image and sends it to AWS Textract Analyze Expense."""
        self.validate_file_size(input_file)
        client = self.create_textract_client()

        logging.info("Reading image bytes from %s and sending to AWS Textract.", input_file)
        with open(input_file, "rb") as img_file:
            img_bytes = img_file.read()

        img_bytes = self.preprocess_image_bytes(img_bytes)
        self.validate_document_bytes_size(img_bytes, input_file)

        try:
            response = client.analyze_expense(Document={"Bytes": img_bytes})
            logging.info("Response successfully received from AWS Textract.")
            return response
        except ClientError as exc:
            error_message = str(exc)
            if hasattr(exc, "response"):
                error_message = exc.response.get("Error", {}).get("Message", error_message)
            raise RuntimeError(f"Communication error with AWS: {error_message}") from exc
        except Exception as exc:
            raise RuntimeError(f"An unexpected error occurred: {exc}") from exc

    def process_image(
        self,
        input_file: str,
        output: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """Processes an invoice image and persists the JSON output."""
        logging.info("Starting extraction for file %s", input_file)
        output_path = self.resolve_output_path(input_file, output)
        response = self.analyze_image(input_file)
        page_count = max(len(response.get("ExpenseDocuments", [])), 1)
        self.last_usage = build_textract_usage_summary(page_count, self.region)
        logging.info("Starting data mapping and parsing phase.")
        final_data = self.parse_expense_data(response)

        if self.last_usage:
            logging.info(
                "AWS Textract usage: pages=%s estimated_cost_usd=%s region=%s",
                self.last_usage.get("page_count"),
                self.last_usage.get("estimated_cost_usd"),
                self.last_usage.get("region"),
            )

        try:
            with open(output_path, "w", encoding="utf-8") as file_handle:
                json.dump(final_data, file_handle, indent=4, ensure_ascii=False)
            return final_data, output_path
        except Exception as exc:
            raise RuntimeError(f"Error saving the output JSON file: {exc}") from exc


def parse_expense_data(response: Dict[str, Any]) -> Dict[str, Any]:
    """Backward-compatible function wrapper around the service parser."""
    return InvoiceExtractionService().parse_expense_data(response)
