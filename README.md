# Invoice/Receipt Extractor (NFC-e/NF-e) via CLI

This is a Python command-line tool that uses the **Amazon Textract Analyze Expense** service to convert Brazilian invoice images (NFC-e/NF-e) into structured data in JSON format.

## Features
- Extraction of header data: Vendor, CNPJ, Date, Total Amount.
- Extraction of consumer data: Name, CPF/CNPJ, and address.
- Extraction of items (products): Description, Amount, Quantity.
- Implements a validation to avoid synchronous requests over the 10 MB AWS Textract limit.
- Validation of the Modulo 11 check digit of the 44-digit access key, if detected in the document.

## Requirements
- Python 3.8+
- An AWS Account with access to Amazon Textract.

## Installation Setup

1.  **Clone the repository or download the files.**
2.  **Create and activate a virtual environment (optional, but recommended):**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows/PowerShell: .\venv\Scripts\activate
    ```
3.  **Install the dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

## AWS Setup (Authentication)

For the tool to work, it needs to be able to access your AWS account. You have a few configuration options:

### Option 1: Configure via AWS CLI (Recommended)
If you have `aws-cli` installed, run:
```bash
aws configure
```
Provide your credentials:
- **AWS Access Key ID**: Your access key.
- **AWS Secret Access Key**: Your corresponding secret key.
- **Default region name**: `us-east-1` (or whichever Textract region you prefer).
- **Default output format**: `json`

### Option 2: Environment Variables
You can export the credentials directly in the terminal before running the script:
```bash
export AWS_ACCESS_KEY_ID="your_access_key"
export AWS_SECRET_ACCESS_KEY="your_secret_key"
export AWS_DEFAULT_REGION="us-east-1"
```

> **Security Warning**: Never commit your active credentials to a public or private repository!

## How to Use

With your credentials configured and dependencies installed, you can simply run the script through the terminal:

```bash
python extract_invoice.py --input path/to/your_invoice.jpg --output result_invoice.json
```

### CLI Arguments
* `--input`: Path to the image file (JPG, PNG) containing the invoice (required).
* `--output`: Output JSON file name or path (optional, default: `result.json`).

### JSON Output Example
```json
{
    "header": {
        "vendor": "EXAMPLE MARKET LTDA",
        "cnpj": "12.345.678/0001-90",
        "date": "10/10/2023",
        "total_amount": "150.00",
        "access_key": "12345678901234567890123456789012345678901234",
        "is_access_key_valid": true
    },
    "items": [
        {
            "description": "PRODUCT 1",
            "amount": "50.00",
            "quantity": "2"
        }
    ],
    "consumer": {
        "name": "FINAL CONSUMER",
        "document": "123.456.789-00",
        "address": "RUA EXEMPLO, 123 - CENTRO - SAO PAULO/SP"
    }
}
```

## Known Limitations
- **Image Size**: Amazon Textract supports incoming images with a maximum of 10MB in synchronous calls.
- **Image Quality (OCR)**: Wrinkled, faded, or low-resolution invoices can cause data extraction errors (e.g., misreading access key numbers or product prices).
