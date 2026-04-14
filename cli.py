import argparse
import logging

try:
    import click
    CLICK_AVAILABLE = True
except ImportError:  # Allows CLI execution even in minimal environments
    click = None
    CLICK_AVAILABLE = False

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


def _run_extraction(input_file: str, output: str | None) -> None:
    """Executes the extraction workflow and reports errors consistently."""
    configure_logging()
    service = InvoiceExtractionService()
    try:
        _, output_path = service.process_image(input_file=input_file, output=output)
        logging.info("Success! Formatted data saved to %s", output_path)
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
    def cli(input_file: str, output: str | None) -> None:
        """Runs the invoice extraction workflow from the command line."""
        _run_extraction(input_file=input_file, output=output)

else:

    def cli(input_file: str, output: str | None = None) -> None:
        """Fallback CLI execution when click is unavailable."""
        _run_extraction(input_file=input_file, output=output)


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
    args = parser.parse_args()
    cli(input_file=args.input_file, output=args.output)


if __name__ == "__main__":
    main()
