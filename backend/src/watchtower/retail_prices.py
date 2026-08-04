"""Azure Retail Prices API client.

Fetches real per-token USD prices for any Azure OpenAI / Foundry model in any region.
Public unauth'd endpoint - no credentials needed.
Cached in-memory 24h since Microsoft updates retail prices at most monthly.

Docs: https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices

Notes on Azure catalog naming (verified 2026-07):
- serviceName = 'Foundry Models' (renamed from 'Cognitive Services' / 'Azure OpenAI')
- meter names use compact abbreviations: 'Inp' = input, 'Outp' = output,
  'glbl' = GlobalStandard, 'regnl' = Regional Standard, 'Batch' = batch,
  'cached' = cached tokens (discounted).
- product name like 'Azure OpenAI gpt 4o 1120 Inp glbl Tokens' where '1120' is the version YYMM.
"""
from __future__ import annotations

import re
import time
from typing import Any
import httpx

BASE_URL = "https://prices.azure.com/api/retail/prices"
CACHE_TTL_SECONDS = 24 * 3600
_LOOKUP_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


def _unit_divisor(unit_of_measure: str) -> int:
    u = (unit_of_measure or "").strip().upper()
    if u.startswith("1K"): return 1_000
    if u.startswith("1M"): return 1_000_000
    return 1


def _tokenize(s: str) -> list[str]:
    """Break a name into normalized tokens: 'gpt-4o-mini' -> ['gpt','4o','mini']."""
    return [t for t in re.split(r"[\s\-_\.]+", (s or "").lower()) if t]


def _model_matches(product: str, meter: str, model_tokens: list[str]) -> bool:
    """The product+meter must contain ALL model name tokens (in any order)."""
    combined = f"{product} {meter}".lower()
    combined_tokens = _tokenize(combined)
    for mt in model_tokens:
        if mt not in combined_tokens and mt not in combined:
            return False
    return True


def _direction_from_meter(meter: str) -> str | None:
    """Return 'input', 'output', or None for meter like 'gpt 4o 1120 Inp glbl Tokens'."""
    m = meter.lower()
    if re.search(r"\binp\b|\binput\b|\bprompt\b", m):
        return "input"
    if re.search(r"\boutp\b|\boutput\b|\bcompletion\b", m):
        return "output"
    return None


def _is_excluded(product: str, meter: str) -> bool:
    """Skip meters that aren't standard inference (we don't want to price against these)."""
    tokens = set(_tokenize(f"{product} {meter}"))
    # Any of these tokens present = not standard inference
    bad_tokens = {"batch", "cached", "cache", "cchd", "ft", "provisioned", "pp", "reserved"}
    if bad_tokens & tokens:
        return True
    combined = f"{product} {meter}".lower()
    for phrase in ("fine tune", "fine-tune", "finetune", "private preview"):
        if phrase in combined:
            return True
    return False


def _sku_tier_bonus(product: str, meter: str, sku_hint: str | None) -> int:
    """When multiple prices match, prefer the one whose tier hint matches (globalstandard vs standard).
    Returns a preference score, higher = better."""
    combined = f"{product} {meter}".lower()
    hint = (sku_hint or "").lower()
    if not hint:
        return 0
    if "global" in hint and "glbl" in combined: return 2
    if "datazone" in hint and "dz" in combined: return 2
    if hint == "standard" and ("regnl" in combined or "regional" in combined): return 2
    return 1


def _query_api(region: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    params: dict[str, Any] | None = {
        "$filter": (
            f"serviceName eq 'Foundry Models' "
            f"and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
    }
    url = BASE_URL
    with httpx.Client(timeout=30) as c:
        pages = 0
        while url and pages < 30:
            r = c.get(url, params=params)
            if r.status_code >= 400:
                break
            data = r.json()
            results.extend(data.get("Items", []))
            url = data.get("NextPageLink")
            params = None
            pages += 1
    return results


# Fallback regions ordered by "closeness" to your deployment region.
# Used when the target region has no published price for the model.
FALLBACK_REGIONS = [
    "eastus2", "eastus", "swedencentral", "northcentralus",
    "westus", "francecentral", "canadaeast", "australiaeast",
]


def _match_in_items(items: list[dict], model_tokens: list[str], sku_hint: str | None) -> tuple[dict[str, float], set[str]]:
    best: dict[str, tuple[float, int]] = {}
    matched: set[str] = set()
    for item in items:
        product = item.get("productName") or ""
        meter = item.get("meterName") or ""
        if _is_excluded(product, meter):
            continue
        if not _model_matches(product, meter, model_tokens):
            continue
        direction = _direction_from_meter(meter)
        if direction is None:
            continue
        retail = float(item.get("retailPrice") or 0)
        if retail <= 0:
            continue
        divisor = _unit_divisor(item.get("unitOfMeasure") or "1K")
        price_per_1m = (retail / divisor) * 1_000_000
        score = _sku_tier_bonus(product, meter, sku_hint)
        matched.add(product)
        cur = best.get(direction)
        # Prefer better tier score (globalstandard match beats regional). Within same score,
        # prefer LOWEST price - the standard SKU is typically the cheapest of the matched
        # variants; going lower is safer than assuming a higher tier.
        if cur is None or score > cur[1] or (score == cur[1] and price_per_1m < cur[0]):
            best[direction] = (price_per_1m, score)
    return {k: v[0] for k, v in best.items()}, matched


def get_pricing_per_1m(model_name: str, region: str, sku_hint: str | None = None) -> dict | None:
    """Return {'input': USD_per_1M, 'output': USD_per_1M, 'source': ..., 'matched_products': [...],
    'priced_in_region': ...} or None if no matching consumption price found in target region OR any fallback."""
    if not model_name or not region:
        return None

    cache_key = (f"{model_name.lower()}|{(sku_hint or '').lower()}", region.lower())
    cached = _LOOKUP_CACHE.get(cache_key)
    now = time.time()
    if cached and (now - cached["cached_at"] < CACHE_TTL_SECONDS):
        return {
            "input": cached["input"], "output": cached["output"],
            "source": "cache", "matched_products": cached.get("matched_products", []),
            "priced_in_region": cached.get("priced_in_region", region),
        }

    model_tokens = _tokenize(model_name)

    # Try target region first
    regions_to_try = [region] + [r for r in FALLBACK_REGIONS if r.lower() != region.lower()]
    for reg in regions_to_try:
        try:
            items = _query_api(reg)
        except Exception:
            continue
        best, matched = _match_in_items(items, model_tokens, sku_hint)
        if not best:
            continue
        input_price = best.get("input") or best.get("output")
        output_price = best.get("output") or best.get("input")

        entry = {
            "input": round(input_price, 6),
            "output": round(output_price, 6),
            "cached_at": now,
            "matched_products": sorted(matched)[:10],
            "priced_in_region": reg,
        }
        _LOOKUP_CACHE[cache_key] = entry
        return {
            "input": entry["input"], "output": entry["output"],
            "source": "retail-api" if reg == region else f"retail-api-fallback:{reg}",
            "matched_products": entry["matched_products"],
            "priced_in_region": reg,
        }

    return None


def clear_cache() -> int:
    n = len(_LOOKUP_CACHE)
    _LOOKUP_CACHE.clear()
    return n


def cache_snapshot() -> list[dict]:
    now = time.time()
    return [
        {
            "key": k[0], "region": k[1],
            "input_per_1m_usd": v["input"], "output_per_1m_usd": v["output"],
            "age_seconds": int(now - v["cached_at"]),
            "matched_products": v.get("matched_products", []),
        }
        for k, v in _LOOKUP_CACHE.items()
    ]
