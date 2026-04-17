import argparse
import logging

try:
    import click
    CLICK_AVAILABLE = True
except ImportError:  # Allows CLI execution even in minimal environments
    click = None
    CLICK_AVAILABLE = False

from services.invoice_qualification_service import InvoiceQualificationService
from services.openai_invoice_service import OpenAIInvoiceExtractionService
from services.invoice_service import InvoiceExtractionService


def configure_logging() -> None:
    """Configures file and console logging for CLI runs."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler("extract_invoice.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def _log_provider_usage(prefix: str, usage: dict | None) -> None:
    """Logs usage and estimated cost for the provider involved in the workflow."""
    if not usage:
        return

    provider = usage.get("provider")
    if provider == "aws_textract":
        logging.info(
            "%s usage | provider=aws_textract api=%s pages=%s region=%s estimated_cost_usd=%s",
            prefix,
            usage.get("api"),
            usage.get("page_count"),
            usage.get("region"),
            usage.get("estimated_cost_usd"),
        )
        return

    logging.info(
        "%s usage | provider=openai model=%s input_tokens=%s output_tokens=%s "
        "total_tokens=%s estimated_cost_usd=%s",
        prefix,
        usage.get("model"),
        usage.get("input_tokens"),
        usage.get("output_tokens"),
        usage.get("total_tokens"),
        usage.get("estimated_cost_usd"),
    )


def _build_extraction_service(method: str):
    """Builds the extraction backend selected by the CLI option."""
    if method == "openai":
        return OpenAIInvoiceExtractionService()
    return InvoiceExtractionService()


def _run_extraction(input_file: str, output: str | None, method: str) -> None:
    """Executes extraction and JSON qualification with consistent error handling."""
    configure_logging()
    extraction_service = _build_extraction_service(method)
    try:
        extracted_data, output_path = extraction_service.process_image(
            input_file=input_file,
            output=output,
        )
        logging.info("Extracted JSON saved to %s", output_path)
        _log_provider_usage(
            "Extraction",
            getattr(extraction_service, "get_last_usage", lambda: None)(),
        )

        if method == "openai":
            _, qualified_path = InvoiceQualificationService.save_qualified_snapshot(
                extracted_data,
                source_output_path=output_path,
            )
            logging.info(
                "Qualified JSON saved to %s (OpenAI extraction already returned the final schema)",
                qualified_path,
            )
        else:
            qualification_service = InvoiceQualificationService()
            _, qualified_path = qualification_service.qualify_and_save(
                extracted_data,
                source_output_path=output_path,
            )
            logging.info("Qualified JSON saved to %s", qualified_path)
            _log_provider_usage(
                "OpenAI qualification",
                qualification_service.get_last_usage(),
            )
    except Exception as exc:
        logging.error("%s", exc)
        raise SystemExit(1) from exc


if CLICK_AVAILABLE:

    @click.command()
    @click.option(
        "--input",
        "input_file",
        required=True,
        type=click.Path(exists=True, dir_okay=False),
        help="Path to the invoice image file",
    )
    @click.option(
        "--output",
        default=None,
        help="Name of the output JSON file. Defaults to the input filename with .json extension.",
    )
    @click.option(
        "--method",
        type=click.Choice(["textract", "openai"], case_sensitive=False),
        default="textract",
        show_default=True,
        help=(
            "Extraction backend: AWS Textract plus OpenAI qualification, "
            "or OpenAI-only extraction."
        ),
    )
    def cli(input_file: str, output: str | None, method: str) -> None:
        """Runs the invoice extraction workflow from the command line."""
        _run_extraction(input_file=input_file, output=output, method=method.lower())

else:

    def cli(
        input_file: str,
        output: str | None = None,
        method: str = "textract",
    ) -> None:
        """Fallback CLI execution when click is unavailable."""
        _run_extraction(input_file=input_file, output=output, method=method.lower())


def main() -> None:
    """Application entrypoint for terminal usage."""
    if CLICK_AVAILABLE:
        cli()
        return

    parser = argparse.ArgumentParser(
        description="Runs the invoice extraction workflow from the command line."
    )
    parser.add_argument(
        "--input",
        dest="input_file",
        required=True,
        help="Path to the invoice image file",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Name of the output JSON file. Defaults to the input filename with .json extension.",
    )
    parser.add_argument(
        "--method",
        choices=["textract", "openai"],
        default="textract",
        help=(
            "Extraction backend: AWS Textract plus OpenAI qualification, "
            "or OpenAI-only extraction."
        ),
    )
    args = parser.parse_args()
    cli(input_file=args.input_file, output=args.output, method=args.method)


if __name__ == "__main__":
    main()
