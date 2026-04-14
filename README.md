# Invoice/Receipt Extractor (NFC-e/NF-e) via CLI

This is a Python command-line tool that uses the **Amazon Textract Analyze Expense** service to convert Brazilian invoice images (NFC-e/NF-e) into structured data in JSON format.

## Features
- Extraction of header data: Vendor, CNPJ, Date, Total Amount.
- Extraction of consumer data: Name, CPF/CNPJ, and address.
- Extraction of items (products): Description, Amount, Quantity.
- OpenAI-based qualification step to review consumer address fields from the ZIP code and sanitize item descriptions.
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

## OpenAI Setup with direnv

The JSON qualification stage reads the OpenAI key from the environment.

1. Install direnv and enable it in your shell.
2. Create a local `.envrc` file in the project root:

```bash
export OPENAI_API_KEY="your_openai_api_key"
export OPENAI_MODEL="gpt-4.1-mini"
```

3. Allow the file once:

```bash
direnv allow
```

> Keep `.envrc` local only. It is ignored by git.

## How to Use

With your credentials configured and dependencies installed, run the command-line entrypoint through the terminal:

```bash
python cli.py --input path/to/your_invoice.jpg --output result_invoice.json
```

The command now writes two files:
- the raw extraction JSON;
- a second qualified JSON named like `nfce_xxx_qualified.json` with corrected consumer address fields and sanitized item descriptions.

### CLI Arguments
* `--input`: Path to the image file (JPG, PNG) containing the invoice (required).
* `--output`: Output JSON file name or path (optional, default: file name generated inside the output folder).

## Project Structure
- service layer: image processing, AWS communication, and response parsing live in the service module;
- CLI entrypoint: terminal execution is handled by the CLI module.

```text
services/
  invoice_service.py
cli.py
```

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
