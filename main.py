import os
import json
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from google import genai
from google.genai import types

# --- Configuration ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

app = FastAPI()

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def read_index():
    return FileResponse("static/index.html")


class ScrapeRequest(BaseModel):
    market: str
    eans: list[str]


# --- Golden Website Registry ---
GOLDEN_SITES = {
    "FR": ["carrefour.fr", "auchan.fr", "coursesu.com", "intermarche.com", "monoprix.fr", "franprix.fr"],
    "UK": ["tesco.com", "sainsburys.co.uk", "asda.com", "morrisons.com", "iceland.co.uk", "waitrose.com"],
    "NL": ["ah.nl", "jumbo.com", "plus.nl", "dirk.nl", "vomar.nl"],
    "BE": ["delhaize.be", "colruyt.be", "carrefour.be", "ah.be"],
    "DE": ["rewe.de", "edeka.de", "kaufland.de", "dm.de", "rossmann.de"],
    "DK": ["nemlig.com", "bilkatogo.dk", "rema1000.dk", "netto.dk"],
    "IT": ["carrefour.it", "conad.it", "esselunga.it", "coop.it"],
    "ES": ["carrefour.es", "mercadona.es", "dia.es", "alcampo.es"],
    "SE": ["ica.se", "coop.se", "willys.se", "hemkop.se"],
    "NO": ["oda.com", "meny.no", "spar.no"],
    "PL": ["carrefour.pl", "auchan.pl", "biedronka.pl"],
    "PT": ["continente.pt", "auchan.pt", "pingo-doce.pt"],
}

CURRENCIES = {
    "FR": "EUR", "NL": "EUR", "BE": "EUR", "DE": "EUR", "IT": "EUR",
    "ES": "EUR", "PT": "EUR",
    "UK": "GBP", "DK": "DKK", "SE": "SEK", "NO": "NOK", "PL": "PLN",
}


def build_scrape_prompt(ean: str, market: str) -> str:
    """Build the prompt encoding the full phased search logic from the gem spec."""
    market_upper = market.upper()
    golden = GOLDEN_SITES.get(market_upper, [])
    golden_str = ", ".join(golden)
    currency = CURRENCIES.get(market_upper, "unknown")
    site_queries = " OR ".join([f"site:{s}" for s in golden])

    return f"""You are a precision price-scraping agent. Find the current regular retail price for the product identified by EAN barcode {ean} in market {market_upper} (currency: {currency}).

CRITICAL RULES:
- Accuracy over completeness. "No data found" is CORRECT only when ALL phases have been exhausted.
- FORBIDDEN: guessing prices, inventing VAT rates, partial EAN matches.
- Only consumer-facing retail prices. No wholesale, "from", or schema-only prices.
- Amazon and eBay third-party sellers are excluded. However, if a niche specialist retailer on eBay is clearly a dedicated retailer (not a random third-party), it may be included and flagged as [Niche].

EXHAUSTIVE SEARCH — YOU MUST EXECUTE ALL PHASES. DO NOT STOP EARLY.
Even if Phase 1 finds results, CONTINUE to later phases to find additional sources.
The goal is to find AS MANY valid retailer sources as possible. More rows = better.

PHASE 1 — GOLDEN WEBSITES:
Search: "{ean}" on these golden retailers: {golden_str}
Try multiple queries:
- "{ean}" {site_queries}
- "{ean}" combined with individual retailer site: operators one by one
- The EAN as a bare number with each retailer
Record ALL valid results found. Then CONTINUE to Phase 2.

PHASE 2 — EXTENDED RETAILERS:
REGARDLESS of Phase 1 results, also search other well-known grocery retailers in {market_upper}.
Search: "{ean}" on any national grocery chain, health food store, pet store, or specialist retailer operating in {market_upper}.
EXCLUDE: Amazon marketplace, eBay random sellers, Bol.com third-party.
Record ALL valid results found. Then CONTINUE to Phase 2.5.

PHASE 2.5 — EAN LOOKUP (product identification):
ALWAYS execute this phase. Search: "{ean}" on openfoodfacts.org, barcodelookup.com, ean-search.org.
Extract: brand, product name, pack size/weight, any retailer links listed.
DO NOT extract prices from these databases.
Use the discovered product name to ALSO search retailers by name (flag results as [Name Match Only]).
Then CONTINUE to Phase 3.

PHASE 3 — NICHE / CLEARANCE / D2C RETAILERS:
ALWAYS execute this phase. Search for the EAN and/or discovered product name on:
- Specialist retailers, pet shops, health food shops, clearance grocers
- Brand D2C websites
- Any other retailer in the correct market with visible pricing
Accept only if EAN is visible OR strict Double Match passes.
Flag appropriately as [Niche], [Clearance], or [D2C].

MATCHING RULES:
Rule A — Explicit EAN: The exact EAN {ean} must be visible on the page or in the search snippet.
Rule B — Double Match Heuristic: If EAN not visible, ALL THREE must match exactly:
  1. Exact Brand
  2. Exact Weight/Volume/Pack Size
  3. Exact Product Title/Variant
DISCARD any source that fails both Rule A and Rule B.
Results found via product name search (not EAN) must be flagged [Name Match Only].

PRICE RULES:
- Report the regular/base consumer price including VAT.
- FORBIDDEN: 2-for-X, 3-for-X, loyalty-only, basket discounts.
- If ONLY a promo price exists: leave rsv_incl_vat empty, set price_type_flag to "Promo-only".
- "Was" prices count as regular price.
- Per-unit price ONLY if pack count is explicitly stated on the same page. Otherwise "Non-comparable".
- Note if a product is out of stock but price is still visible — include it with a note in vendor_name like "VetUK (Out of stock)".

OUTPUT — Return ONLY a valid JSON object with NO markdown formatting, NO code blocks, NO explanation before or after. The JSON must use this exact structure:
{{
  "ean": "{ean}",
  "master_data": {{
    "brand": "brand name or empty string if unknown",
    "product_name": "full product name or empty string",
    "pack_format": "e.g. 150g, 6 x 330ml, or empty string"
  }},
  "status": "found" or "no_data",
  "ean_valid": true or false,
  "phase_4_available": true or false,
  "prices": [
    {{
      "vendor_name": "Retailer Name (or 'Retailer Name (Out of stock)' if applicable)",
      "market": "{market_upper}",
      "currency": "{currency}",
      "rsv_incl_vat": "numeric price as string e.g. 2.00, or empty string if promo-only",
      "vat_info": "incl. VAT (rate shown: X%)" or "incl. VAT (rate not stated)",
      "promo_price": "promo price as string, or empty string",
      "price_type_flag": "Regular" or "Promo-only" or "Non-comparable" or "[Name Match Only]" or "[Clearance]" or "[D2C]" or "[Niche]" or "[Niche] [Name Match Only]",
      "pack_format": "e.g. 150g",
      "per_unit_rsv": "per-unit price as string, or Non-comparable",
      "source_url": "full URL where price is visible"
    }}
  ]
}}

Include EVERY valid source found across ALL phases. Aim for multiple rows per EAN.
If truly no prices found after exhausting all phases, return status "no_data" with empty prices array.
Search THOROUGHLY — execute at least 8-10 different search queries before concluding no data."""


def parse_gemini_json(text: str) -> dict:
    """Robustly extract JSON from Gemini response text."""
    cleaned = text.strip()

    # Strip markdown code blocks
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1]
        if "```" in cleaned:
            cleaned = cleaned.split("```", 1)[0]
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1]
        if "```" in cleaned:
            cleaned = cleaned.split("```", 1)[0]

    cleaned = cleaned.strip()

    # Find outermost JSON object
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("No JSON object found in response")

    # Find matching closing brace
    depth = 0
    for i in range(start, len(cleaned)):
        if cleaned[i] == "{":
            depth += 1
        elif cleaned[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start:i + 1])

    # Fallback: try rfind
    end = cleaned.rfind("}") + 1
    if end > start:
        return json.loads(cleaned[start:end])

    raise ValueError("Could not extract valid JSON")


async def call_gemini_for_ean(ean: str, market: str) -> dict:
    """Call Gemini API with Google Search grounding to scrape price for one EAN."""
    empty_result = {
        "ean": ean, "status": "error",
        "prices": [], "master_data": {"brand": "", "product_name": "", "pack_format": ""}
    }

    if not GEMINI_API_KEY:
        empty_result["error"] = "GEMINI_API_KEY not configured"
        return empty_result

    prompt = build_scrape_prompt(ean, market)

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)

        grounding_tool = types.Tool(google_search=types.GoogleSearch())

        config = types.GenerateContentConfig(
            tools=[grounding_tool],
            temperature=0.1,
        )

        # Run synchronous Gemini call in a thread to not block the event loop
        def _call():
            return client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=config,
            )

        response = await asyncio.get_event_loop().run_in_executor(None, _call)

        if not response or not response.text:
            empty_result["error"] = "Empty response from Gemini"
            return empty_result

        result = parse_gemini_json(response.text)
        result["ean"] = ean

        # Enrich source URLs from grounding metadata where possible
        grounding_urls = []
        try:
            if response.candidates and response.candidates[0].grounding_metadata:
                gm = response.candidates[0].grounding_metadata
                if gm.grounding_chunks:
                    for chunk in gm.grounding_chunks:
                        if hasattr(chunk, 'web') and chunk.web and hasattr(chunk.web, 'uri'):
                            grounding_urls.append(chunk.web.uri)
        except Exception:
            pass

        if grounding_urls and result.get("prices"):
            for price_entry in result["prices"]:
                if not price_entry.get("source_url"):
                    vendor = price_entry.get("vendor_name", "").lower().split()[0] if price_entry.get("vendor_name") else ""
                    for url in grounding_urls:
                        if vendor and vendor in url.lower():
                            price_entry["source_url"] = url
                            break

        return result

    except json.JSONDecodeError as e:
        empty_result["error"] = f"JSON parse error: {str(e)}"
        return empty_result
    except Exception as e:
        empty_result["error"] = str(e)
        return empty_result


def compute_averages(all_results: list[dict]) -> dict:
    """Compute average RSV per EAN following spec rules."""
    averages = {}
    for result in all_results:
        ean = result.get("ean", "")
        prices = result.get("prices", [])
        if not prices:
            averages[ean] = {"standard_avg": None, "total_avg": None, "count": 0}
            continue

        standard_prices = []
        all_valid_prices = []

        for p in prices:
            flag = p.get("price_type_flag", "")
            rsv = p.get("rsv_incl_vat", "")
            if not rsv or flag in ("Promo-only", "Non-comparable"):
                continue

            try:
                val = float(str(rsv).replace(",", ".").replace("£", "").replace("€", "").replace("kr", "").strip())
            except (ValueError, AttributeError):
                continue

            all_valid_prices.append(val)
            if flag not in ("[Clearance]", "[D2C]"):
                standard_prices.append(val)

        std_avg = round(sum(standard_prices) / len(standard_prices), 2) if standard_prices else None
        total_avg = round(sum(all_valid_prices) / len(all_valid_prices), 2) if all_valid_prices else None

        averages[ean] = {
            "standard_avg": std_avg,
            "total_avg": total_avg,
            "count": len(all_valid_prices)
        }

    return averages


@app.post("/scrape")
async def run_scraper(request: ScrapeRequest):
    market = request.market.upper().strip()
    eans = [e.strip() for e in request.eans if e.strip()]

    if len(eans) > 10:
        raise HTTPException(status_code=400, detail="Max 10 EANs per request")
    if not eans:
        raise HTTPException(status_code=400, detail="No EANs provided")
    if market not in GOLDEN_SITES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported market. Supported: {', '.join(sorted(GOLDEN_SITES.keys()))}"
        )

    # Process EANs concurrently (max 3 at a time to respect rate limits)
    semaphore = asyncio.Semaphore(3)

    async def limited_call(ean):
        async with semaphore:
            return await call_gemini_for_ean(ean, market)

    tasks = [limited_call(ean) for ean in eans]
    results = await asyncio.gather(*tasks)

    averages = compute_averages(results)

    return {
        "market": market,
        "currency": CURRENCIES.get(market, "unknown"),
        "results": results,
        "averages": averages
    }
