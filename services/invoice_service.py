import json
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple

try:
    import boto3
except ImportError:  # Allows parser-only usage in minimal environments
    boto3 = None

try:
    from botocore.exceptions import ClientError
except ImportError:
    ClientError = Exception


class InvoiceExtractionService:
    """Service layer for invoice OCR parsing and AWS Textract image processing."""

    MAX_TEXTRACT_FILE_SIZE_MB = 10.0

    def __init__(self, region: Optional[str] = None) -> None:
        self.region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

    @staticmethod
    def normalize_text(value: str) -> str:
        """Collapses OCR whitespace and control characters into a clean single-line string."""
        normalized = str(value).replace("\u00a0", " ")
        normalized = re.sub(r"[\r\n\t]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

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

        return not self.is_street_candidate(normalized)

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

        return item if item else None

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
            if any(kw in upper_text for kw in ["DESTINATA", "CONSUMIDOR"]):
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
                elif (
                    "name" not in data["consumer"]
                    and self.is_name_candidate(text)
                ):
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
        logging.info("Starting data mapping and parsing phase.")
        final_data = self.parse_expense_data(response)

        try:
            with open(output_path, "w", encoding="utf-8") as file_handle:
                json.dump(final_data, file_handle, indent=4, ensure_ascii=False)
            return final_data, output_path
        except Exception as exc:
            raise RuntimeError(f"Error saving the output JSON file: {exc}") from exc


def parse_expense_data(response: Dict[str, Any]) -> Dict[str, Any]:
    """Backward-compatible function wrapper around the service parser."""
    return InvoiceExtractionService().parse_expense_data(response)
