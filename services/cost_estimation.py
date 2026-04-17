from typing import Any, Dict, Optional


OPENAI_AND_AWS_COST_SURCHARGE_RATE = 0.37
AWS_TEXTRACT_ANALYZE_EXPENSE_PRICE_PER_PAGE_USD = 0.01


def apply_cost_surcharge(base_cost_usd: Optional[float]) -> Optional[float]:
    """Applies the configured surcharge to a base provider cost."""
    if base_cost_usd is None:
        return None
    return base_cost_usd * (1 + OPENAI_AND_AWS_COST_SURCHARGE_RATE)


def estimate_textract_analyze_expense_cost_usd(page_count: int) -> Optional[float]:
    """Estimates AWS Textract Analyze Expense cost using public per-page pricing."""
    if page_count <= 0:
        return None
    base_cost = page_count * AWS_TEXTRACT_ANALYZE_EXPENSE_PRICE_PER_PAGE_USD
    return apply_cost_surcharge(base_cost)


def build_textract_usage_summary(
    page_count: int,
    region: str,
) -> Optional[Dict[str, Any]]:
    """Builds a normalized usage summary for AWS Textract Analyze Expense."""
    estimated_cost_usd = estimate_textract_analyze_expense_cost_usd(page_count)
    if estimated_cost_usd is None:
        return None

    return {
        "provider": "aws_textract",
        "api": "analyze_expense",
        "region": region,
        "page_count": page_count,
        "estimated_cost_usd": estimated_cost_usd,
    }
