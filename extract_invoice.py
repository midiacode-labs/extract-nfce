import os
import re
import json
import logging

try:
    import boto3
except ImportError:  # Allows parser-only usage in minimal environments
    boto3 = None

try:
    import click
except ImportError:  # Allows importing parsing helpers during tests
    class _ClickFallback:
        @staticmethod
        def command(*args, **kwargs):
            def decorator(func):
                return func
            return decorator

        @staticmethod
        def option(*args, **kwargs):
            def decorator(func):
                return func
            return decorator

        @staticmethod
        def Path(*args, **kwargs):
            return None

    click = _ClickFallback()

try:
    from botocore.exceptions import ClientError
except ImportError:
    ClientError = Exception


def normalize_text(value: str) -> str:
    """Collapses OCR whitespace and control characters into a clean single-line string."""
    normalized = str(value).replace('\u00a0', ' ')
    normalized = re.sub(r'[\r\n\t]+', ' ', normalized)
    normalized = re.sub(r'\s+', ' ', normalized)
    return normalized.strip()


def extract_date(text: str):
    """Extracts the first invoice-like date from OCR text."""
    match = re.search(r'\b\d{2}/\d{2}/\d{4}\b', text)
    return match.group() if match else None


def set_header_issue_date(header_data: dict, value: str) -> None:
    """Stores the invoice issue date in the header using stable keys."""
    issue_date = extract_date(value)
    if issue_date:
        header_data["date"] = issue_date
        header_data["issue_date"] = issue_date


def validate_access_key(key: str) -> bool:
    """Validates the check digit of an electronic invoice access key (44 digits)."""
    key = re.sub(r'\D', '', key)
    if len(key) != 44:
        return False

    # Calculate Check Digit (Modulo 11)
    # Weight for multiplication: 2 to 9, right to left,
    # skipping the last digit (which is the check digit itself).
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


def extract_document_number(text: str):
    """Extracts CPF/CNPJ values from a text snippet."""
    match = re.search(
        r'\d{3}\.\d{3}\.\d{3}-\d{2}|\b\d{11}\b|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}',
        text,
    )
    return match.group() if match else None


def extract_cnpj(text: str):
    """Extracts only CNPJ values from a text snippet."""
    match = re.search(r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\b\d{14}\b', text)
    return match.group() if match else None


def is_cnpj_document(value: str) -> bool:
    """Returns True when the value is a Brazilian CNPJ."""
    return len(re.sub(r'\D', '', value or '')) == 14


def assign_detected_document(text: str, consumer_data: dict, header_data: dict) -> None:
    """Routes OCR-detected documents to the correct JSON section."""
    document = extract_document_number(text)
    if not document:
        return

    upper_text = normalize_text(text).upper()
    if "CPF" in upper_text:
        consumer_data.setdefault("document", document)
        return

    if is_cnpj_document(document):
        consumer_markers = [
            "DESTINAT",
            "RECEIVER",
            "BUYER",
            "CUSTOMER",
            "CLIENTE",
        ]
        is_consumer_cnpj = (
            any(marker in upper_text for marker in consumer_markers)
            and "CONSUMIDOR FINAL" not in upper_text
        )
        if is_consumer_cnpj:
            consumer_data.setdefault("document", document)
        else:
            header_data.setdefault("cnpj", document)
        return

    consumer_data.setdefault("document", document)


def is_date_or_time(text: str) -> bool:
    """Returns True for pure date/time OCR lines that should not become address."""
    normalized = re.sub(r'\s+', ' ', text).strip()
    return bool(
        re.fullmatch(r'\d{1,2}/\d{1,2}/\d{2,4}', normalized)
        or re.fullmatch(r'\d{1,2}:\d{2}(?::\d{2})?', normalized)
    )


def is_zip_code(text: str) -> bool:
    """Returns True when the text looks like a Brazilian CEP."""
    normalized = re.sub(r'\s+', '', text)
    return bool(re.fullmatch(r'\d{5}-?\d{3}', normalized))


def is_state_code(text: str) -> bool:
    """Returns True when the text looks like a valid Brazilian UF code."""
    normalized = re.sub(r'\s+', ' ', text).strip().upper()
    valid_states = {
        "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA",
        "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN",
        "RS", "RO", "RR", "SC", "SP", "SE", "TO",
    }
    return normalized in valid_states


def is_street_candidate(text: str) -> bool:
    """Returns True when the OCR line looks like a street/address line."""
    normalized = re.sub(r'\s+', ' ', text).strip().upper()
    if len(normalized) < 5 or is_date_or_time(normalized):
        return False

    if any(keyword in normalized for keyword in ["CPF", "CNPJ", "CONSUMIDOR", "DESTINAT"]):
        return False

    address_keywords = [
        "RUA", "AV ", "AV.", "AVENIDA", "ALAMEDA", "ESTRADA", "RODOVIA",
        "TRAVESSA", "TV ", "PRACA", "PRAÇA", "APTO", "BLOCO", "CASA",
        "LOTE", "QUADRA", "NUMERO", "NRO"
    ]
    return any(keyword in normalized for keyword in address_keywords)


def is_location_candidate(text: str) -> bool:
    """Returns True when the OCR line looks like city or neighborhood info."""
    normalized = re.sub(r'\s+', ' ', text).strip()
    if len(normalized) < 4 or re.search(r'\d', normalized) or is_date_or_time(normalized):
        return False

    upper_text = normalized.upper()
    blocked_keywords = [
        "CPF", "CNPJ", "CONSUMIDOR", "DESTINAT", "CALCULO", "CÁLCULO",
        "VALOR", "TOTAL", "TRIB", "INSCRICAO", "INSCRIÇÃO", "DATA",
        "CHAVE", "DANFE", "NOTA", "CIT"
    ]
    if any(keyword in upper_text for keyword in blocked_keywords):
        return False

    words = [word for word in re.split(r'\s+', normalized) if word]
    if normalized == normalized.upper() and words and all(len(word) <= 4 for word in words):
        return False

    return True


def is_name_candidate(text: str) -> bool:
    """Returns True when the OCR line looks like a consumer name."""
    normalized = re.sub(r'\s+', ' ', text).strip()
    parts = [part for part in normalized.split() if part]
    if len(normalized) < 10 or len(parts) < 2 or re.search(r'\d', normalized):
        return False

    upper_text = normalized.upper()
    if any(keyword in upper_text for keyword in ["CPF", "CNPJ", "CONSUMIDOR", "DESTINAT"]):
        return False

    return not is_street_candidate(normalized)


def compose_consumer_address(consumer_data: dict) -> None:
    """Builds a stable full address string from extracted consumer components."""
    ordered_keys = ["street", "zip_code", "city", "neighborhood", "state"]
    parts = []
    seen = set()

    for key in ordered_keys:
        value = consumer_data.get(key)
        if value:
            cleaned = re.sub(r'\s+', ' ', value).strip(' ,;-')
            if cleaned and cleaned not in seen:
                parts.append(cleaned)
                seen.add(cleaned)

    if parts:
        consumer_data["address"] = ", ".join(parts)


def update_consumer_address(consumer_data: dict, value: str) -> None:
    """Stores consumer address components from OCR or summary fields."""
    cleaned = re.sub(r'\s+', ' ', value).strip(' ,;-')
    if not cleaned or is_date_or_time(cleaned) or extract_document_number(cleaned):
        return

    if cleaned == consumer_data.get("name"):
        return

    upper_text = cleaned.upper()
    neighborhood_keywords = ["BAIRRO", "JARDIM", "JD", "VILA", "PARQUE", "CENTRO"]
    has_address_anchor = any(key in consumer_data for key in ["street", "zip_code", "state"])

    if is_zip_code(cleaned):
        consumer_data["zip_code"] = cleaned
    elif is_state_code(cleaned):
        consumer_data["state"] = upper_text
    elif is_street_candidate(cleaned):
        consumer_data.setdefault("street", cleaned)
    elif has_address_anchor and any(keyword in upper_text for keyword in neighborhood_keywords):
        consumer_data.setdefault("neighborhood", cleaned)
    elif has_address_anchor and is_location_candidate(cleaned):
        if "city" not in consumer_data:
            consumer_data["city"] = cleaned
        else:
            consumer_data.setdefault("neighborhood", cleaned)

    compose_consumer_address(consumer_data)


def parse_expense_data(response):
    extracted_data = {
        "header": {},
        "items": [],
        "consumer": {},
    }

    for doc in response.get('ExpenseDocuments', []):
        # 1. SummaryFields (Header and other general data)
        for field in doc.get('SummaryFields', []):
            if 'Type' not in field or 'ValueDetection' not in field:
                continue

            type_name = normalize_text(field['Type']['Text'])
            value = normalize_text(field['ValueDetection']['Text'])

            # Header Mapping
            if type_name == "VENDOR_NAME":
                extracted_data["header"]["vendor"] = value
            elif type_name == "VENDOR_VAT_NUMBER":
                extracted_data["header"]["cnpj"] = extract_cnpj(value) or value
            elif type_name == "VENDOR_ADDRESS" and "cnpj" not in extracted_data["header"]:
                # Fallback in case the CNPJ appears inside the vendor address.
                cnpj = extract_cnpj(value)
                if cnpj:
                    extracted_data["header"]["cnpj"] = cnpj
            elif type_name in ("INVOICE_RECEIPT_DATE", "INVOICE_DATE"):
                set_header_issue_date(extracted_data["header"], value)
            elif type_name == "TOTAL":
                extracted_data["header"]["total_amount"] = value

            # Consumer Mapping
            elif type_name in ("CUSTOMER_NAME", "RECEIVER_NAME", "NAME"):
                extracted_data["consumer"]["name"] = value
            elif type_name == "RECEIVER_VAT_NUMBER":
                extracted_data["consumer"]["document"] = value
            elif type_name in ("RECEIVER_ADDRESS", "CUSTOMER_ADDRESS", "BUYER_ADDRESS"):
                update_consumer_address(extracted_data["consumer"], value)
            elif any(keyword in type_name.upper() for keyword in ["ZIP", "POSTAL", "CEP"]):
                extracted_data["consumer"]["zip_code"] = value.strip()
                compose_consumer_address(extracted_data["consumer"])
            elif any(
                keyword in type_name.upper()
                for keyword in ["CITY", "MUNICIPALITY"]
            ):
                extracted_data["consumer"]["city"] = value.strip()
                compose_consumer_address(extracted_data["consumer"])
            elif any(
                keyword in type_name.upper()
                for keyword in ["NEIGHBOR", "DISTRICT", "BAIRRO"]
            ):
                extracted_data["consumer"]["neighborhood"] = value.strip()
                compose_consumer_address(extracted_data["consumer"])
            elif any(keyword in type_name.upper() for keyword in ["STATE", "PROVINCE", "UF"]):
                extracted_data["consumer"]["state"] = value.strip().upper()
                compose_consumer_address(extracted_data["consumer"])
            elif "ADDRESS" in type_name.upper() and not type_name.upper().startswith("VENDOR"):
                update_consumer_address(extracted_data["consumer"], value)

            # Attempt to find CPF or Document via other summary field types
            elif any(
                k in type_name.upper()
                for k in ["CPF", "CONSUMIDOR", "CONSUMER", "DESTINATARIO", "DESTINATÁRIO"]
            ):
                extracted_data["consumer"]["document"] = value
            elif (
                any(
                    k in value.upper()
                    for k in ["CPF", "CNPJ", "DESTINATARIO", "DESTINATÁRIO"]
                )
                and type_name in ("OTHER", "CUSTOMER_NUMBER")
            ):
                assign_detected_document(
                    value,
                    extracted_data["consumer"],
                    extracted_data["header"],
                )
            elif type_name == "OTHER":
                document = extract_document_number(value)
                if document:
                    assign_detected_document(
                        value,
                        extracted_data["consumer"],
                        extracted_data["header"],
                    )
                elif extract_date(value) and "issue_date" not in extracted_data["header"]:
                    set_header_issue_date(extracted_data["header"], value)
                else:
                    update_consumer_address(extracted_data["consumer"], value)

        # 2. LineItems (Invoice items)
        for group in doc.get('LineItemGroups', []):
            for line_item in group.get('LineItems', []):
                item_details = {}
                for expense_field in line_item.get('LineItemExpenseFields', []):
                    if 'Type' not in expense_field or 'ValueDetection' not in expense_field:
                        continue
                    item_type = normalize_text(expense_field['Type']['Text'])
                    item_val = normalize_text(expense_field['ValueDetection']['Text'])

                    if item_type == "ITEM":
                        item_details["description"] = item_val
                    elif item_type == "PRICE":
                        item_details["amount"] = item_val
                    elif item_type == "QUANTITY":
                        item_details["quantity"] = item_val
                    else:
                        item_details[item_type.lower()] = item_val

                if item_details:
                    extracted_data["items"].append(item_details)

        # 3. Access Key Extraction via Text. Textract returns raw text blocks
        # This is useful when the key wasn't in structured fields and quality is compromised
        consumer_lines_to_check = 0
        for block in doc.get('Blocks', []):
            if block['BlockType'] == 'LINE':
                # Search for 44-digit key, ignoring spaces
                text = normalize_text(block.get('Text', ''))
                upper_text = text.upper()
                numbers = re.sub(r'\s+', '', text)
                if len(numbers) == 44 and numbers.isdigit():
                    extracted_data["header"]["access_key"] = numbers
                    extracted_data["header"]["is_access_key_valid"] = validate_access_key(numbers)

                cnpj = extract_cnpj(text)
                if cnpj and "cnpj" not in extracted_data["header"]:
                    extracted_data["header"]["cnpj"] = cnpj

                if "issue_date" not in extracted_data["header"] and extract_date(text):
                    set_header_issue_date(extracted_data["header"], text)

                # Search for consumer data in nearby text lines as OCR fallback
                if any(k in upper_text for k in ["DESTINATA", "CONSUMIDOR"]):
                    consumer_lines_to_check = 15
                    continue
                elif consumer_lines_to_check > 0:
                    document = extract_document_number(text)
                    if document:
                        assign_detected_document(
                            text,
                            extracted_data["consumer"],
                            extracted_data["header"],
                        )
                    elif "name" not in extracted_data["consumer"] and is_name_candidate(text):
                        extracted_data["consumer"]["name"] = text.strip()
                    else:
                        update_consumer_address(extracted_data["consumer"], text)
                    consumer_lines_to_check -= 1

    return extracted_data


@click.command()
@click.option(
    '--input',
    'input_file',
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help='Path to the invoice image file',
)
@click.option(
    '--output',
    default=None,
    help='Name of the output JSON file. Defaults to input filename with .json extension.',
)
def main(input_file, output):
    """
    Invoice Extractor using AWS Textract (Analyze Expense).
    """
    project_root = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(project_root, "output")
    os.makedirs(output_dir, exist_ok=True)

    if not output:
        base_name = os.path.splitext(os.path.basename(input_file))[0]
        output = os.path.join(output_dir, f"{base_name}.json")
    elif not os.path.dirname(output):
        output = os.path.join(output_dir, output)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler("extract_invoice.log", encoding='utf-8'),
            logging.StreamHandler()
        ]
    )

    logging.info(f"Starting extraction for file: {input_file}")

    # 1. Validate image size (Textract limit is 10MB for synchronous base64/bytes calls)
    file_size_mb = os.path.getsize(input_file) / (1024 * 1024)
    if file_size_mb > 10.0:
        logging.error(
            "File '%s' exceeds the 10MB limit supported by AWS Textract "
            "(Current size: %.2f MB).",
            input_file,
            file_size_mb,
        )
        return

    if boto3 is None:
        logging.error(
            "boto3 is not installed in the active environment. "
            "Install the project requirements before running the CLI."
        )
        return

    # Initialize boto3 client
    try:
        region = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
        client = boto3.client('textract', region_name=region)
        logging.info(f"AWS Textract client initialized (Region: {region}).")
    except Exception as e:
        logging.error(f"Error initializing AWS client: {str(e)}")
        return

    logging.info("Reading image bytes from '%s' and sending to AWS Textract...", input_file)

    with open(input_file, 'rb') as img_file:
        img_bytes = img_file.read()

    try:
        response = client.analyze_expense(Document={'Bytes': img_bytes})
        logging.info("Response successfully received from AWS Textract.")
    except ClientError as e:
        logging.error("Communication error with AWS: %s", e.response['Error']['Message'])
        return
    except Exception as e:
        logging.error("An unexpected error occurred: %s", str(e))
        return

    logging.info("Starting data mapping (parsing) phase...")
    final_data = parse_expense_data(response)

    try:
        with open(output, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, indent=4, ensure_ascii=False)
        logging.info("Success! Formatted data saved to %s", output)
    except Exception as e:
        logging.error("Error saving the output JSON file: %s", str(e))


if __name__ == '__main__':
    main()
