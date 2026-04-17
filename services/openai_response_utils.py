import json
import re
from typing import Any, Dict, Optional

from services.cost_estimation import apply_cost_surcharge

MODEL_PRICING_USD_PER_1M_TOKENS = {
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
}


def extract_response_text(response: Any) -> str:
    """Extracts plain text from an OpenAI Responses API object."""
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


def parse_json_response(text: str) -> Dict[str, Any]:
    """Parses JSON content that may arrive wrapped in Markdown fences."""
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


def _read_usage_field(usage: Any, field: str) -> Optional[int]:
    if usage is None:
        return None
    if isinstance(usage, dict):
        value = usage.get(field)
    else:
        value = getattr(usage, field, None)
    return int(value) if value is not None else None


def estimate_cost_usd(
    model: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
) -> Optional[float]:
    """Estimates token cost using the pricing table plus the configured surcharge."""
    if input_tokens is None and output_tokens is None:
        return None

    pricing = None
    for prefix in sorted(MODEL_PRICING_USD_PER_1M_TOKENS, key=len, reverse=True):
        if model.startswith(prefix):
            pricing = MODEL_PRICING_USD_PER_1M_TOKENS[prefix]
            break

    if pricing is None:
        return None

    total_cost = 0.0
    if input_tokens is not None:
        total_cost += (input_tokens / 1_000_000) * pricing["input"]
    if output_tokens is not None:
        total_cost += (output_tokens / 1_000_000) * pricing["output"]
    return apply_cost_surcharge(total_cost)


def build_usage_summary(response: Any, model: str) -> Optional[Dict[str, Any]]:
    """Builds a normalized token usage summary from an OpenAI response."""
    usage = getattr(response, "usage", None)
    input_tokens = _read_usage_field(usage, "input_tokens")
    output_tokens = _read_usage_field(usage, "output_tokens")
    total_tokens = _read_usage_field(usage, "total_tokens")

    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)

    estimated_cost_usd = estimate_cost_usd(model, input_tokens, output_tokens)
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None

    return {
        "provider": "openai",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "estimated_cost_usd": estimated_cost_usd,
    }
