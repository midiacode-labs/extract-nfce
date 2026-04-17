# Invoice/Receipt Extractor (NFC-e/NF-e) via CLI and Streamlit

This project provides both a **Python CLI** and a **Streamlit web interface** to convert Brazilian invoice images (NFC-e/NF-e) into structured JSON data using either **Amazon Textract Analyze Expense** or an **OpenAI-only image extraction flow**.

## Features
- Extraction of header data: Vendor, CNPJ, Date, Total Amount.
- Extraction of consumer data: Name, CPF/CNPJ, and address.
- Extraction of items (products): Description, Amount, Quantity.
- OpenAI-based qualification step to review consumer address fields from the ZIP code and sanitize item descriptions.
- OpenAI-only extraction mode that sends the image directly to the OpenAI API and returns the full invoice structure without AWS Textract.
- Streamlit UI for uploading an image or capturing one directly from the device camera.
- Selection of extraction backend in both Streamlit and CLI.
- Estimated AWS Textract and OpenAI cost reporting in the UI, with a 37% surcharge applied.
- Invoice preview with zoom control, execution timing, item counters, and reset workflow.
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

The JSON qualification stage and the OpenAI-only extraction flow read the OpenAI key from the environment.

1. Install direnv and enable it in your shell.
2. Create a local `.envrc` file in the project root:

```bash
export OPENAI_API_KEY="your_openai_api_key"
export OPENAI_MODEL="gpt-4.1-mini"
export OPENAI_EXTRACTION_MODEL="gpt-4.1-mini"
```

3. Allow the file once:

```bash
direnv allow
```

> Keep `.envrc` local only. It is ignored by git.

## How to Use

With your credentials configured and dependencies installed, you can choose between the web UI and the CLI.

### Option 1: Run the Streamlit Web App

Start the interface with:

```bash
streamlit run streamlit_app.py
```

The web app allows you to:
- upload a JPG, JPEG, or PNG invoice image;
- capture the invoice using the device camera;
- choose between `AWS Textract + OpenAI` and `OpenAI somente`;
- preview the image with zoom;
- process the invoice end-to-end automatically after upload;
- review estimated AWS Textract cost, estimated OpenAI cost, and total estimated cost;
- review OpenAI token usage whenever the selected flow includes OpenAI.

### Option 2: Run via CLI

Use the command-line entrypoint through the terminal:

```bash
python cli.py --input path/to/your_invoice.jpg --output result_invoice.json --method textract

python cli.py --input path/to/your_invoice.jpg --output result_invoice.json --method openai
```

The workflow writes two files:
- the raw extraction JSON;
- a second qualified JSON named like `nfce_xxx_qualified.json` with corrected consumer address fields and sanitized item descriptions.

### CLI Arguments
* `--input`: Path to the image file (JPG, PNG) containing the invoice (required).
* `--output`: Output JSON file name or path (optional, default: file name generated inside the output folder).
* `--method`: Extraction backend. Use `textract` for AWS Textract plus OpenAI qualification, or `openai` for the OpenAI-only image extraction flow.

## Project Structure
- service layer: image processing, AWS communication, and response parsing live in the service module;
- qualification layer: post-processing and address/item cleanup live in the qualification service;
- OpenAI-only extraction layer: direct image-to-JSON extraction lives in a dedicated service;
- CLI entrypoint: terminal execution is handled by the CLI module;
- Streamlit entrypoint: interactive web execution is handled by the Streamlit app.

```text
services/
  invoice_service.py
  invoice_qualification_service.py
    openai_invoice_service.py
cli.py
streamlit_app.py
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

## Streamlit Workflow Summary
1. Open the app in the browser.
2. Choose the extraction backend.
3. Upload an invoice image or capture one with the camera.
4. Wait for the selected workflow to finish automatically.
5. Review the qualified result, token usage, and estimated OpenAI cost when available.

## Known Limitations
- **Image Size**: Amazon Textract supports incoming images with a maximum of 10MB in synchronous calls.
- **Image Quality (OCR)**: Wrinkled, faded, or low-resolution invoices can cause data extraction errors (e.g., misreading access key numbers or product prices).
