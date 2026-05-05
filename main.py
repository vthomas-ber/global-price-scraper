import os
import json
import asyncio
import logging
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("price-scraper")

# --- Configuration ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")

# Try importing google-genai SDK; fall back to REST API if unavailable
try:
    from google import genai
    from google.genai import types
    HAS_SDK = True
    logger.info("google-genai SDK loaded")
except ImportError:
    HAS_SDK = False
    logger.info("google-genai SDK not found, using REST API")

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
        raise ValueError(f"No JSON object found. Text starts with: {cleaned[:200]}")

    # Find matching closing brace
    depth = 0
    for i in range(start, len(cleaned)):
        if cleaned[i] == "{":
            depth += 1
        elif cleaned[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start:i + 1])

    end = cleaned.rfind("}") + 1
    if end > start:
        return json.loads(cleaned[start:end])

    raise ValueError(f"Could not extract valid JSON. Text: {cleaned[:300]}")


async def call_gemini_rest(ean: str, market: str, prompt: str) -> dict:
    """Call Gemini via REST API directly — more reliable than SDK for grounded search."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1}
    }

    params = {"key": GEMINI_API_KEY}

    async with httpx.AsyncClient() as client:
        logger.info(f"[{ean}] REST API call to {GEMINI_MODEL}...")
        res = await client.post(url, json=payload, params=params, timeout=120.0)

        if res.status_code != 200:
            error_text = res.text[:500]
            logger.error(f"[{ean}] REST API error {res.status_code}: {error_text}")
            raise Exception(f"Gemini API {res.status_code}: {error_text}")

        data = res.json()

        # Extract text from response
        text_parts = []
        candidates = data.get("candidates", [])
        if not candidates:
            logger.warning(f"[{ean}] No candidates in response")
            raise Exception("No candidates in Gemini response")

        candidate = candidates[0]
        content = candidate.get("content", {})
        parts = content.get("parts", [])

        for part in parts:
            if "text" in part:
                text_parts.append(part["text"])

        response_text = "\n".join(text_parts)

        if not response_text:
            # Log what we got for debugging
            finish_reason = candidate.get("finishReason", "unknown")
            grounding = candidate.get("groundingMetadata", {})
            queries = grounding.get("webSearchQueries", [])
            logger.warning(f"[{ean}] Empty text. finish={finish_reason}, queries={queries}")
            raise Exception(f"Empty response (finish={finish_reason}, searched={len(queries)} queries)")

        logger.info(f"[{ean}] Got {len(response_text)} chars from REST API")

        # Extract grounding URLs
        grounding_urls = []
        grounding_meta = candidate.get("groundingMetadata", {})
        for chunk in grounding_meta.get("groundingChunks", []):
            web = chunk.get("web", {})
            if web.get("uri"):
                grounding_urls.append(web["uri"])

        search_queries = grounding_meta.get("webSearchQueries", [])
        if search_queries:
            logger.info(f"[{ean}] Gemini searched: {search_queries}")

        return {
            "text": response_text,
            "grounding_urls": grounding_urls
        }


async def call_gemini_sdk(ean: str, market: str, prompt: str) -> dict:
    """Call Gemini via the Python SDK."""
    client = genai.Client(api_key=GEMINI_API_KEY)

    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.1,
    )

    def _call():
        return client.models.generate_content(
            model=GEMINI_MODEL, contents=prompt, config=config,
        )

    logger.info(f"[{ean}] SDK call to {GEMINI_MODEL}...")
    response = await asyncio.get_event_loop().run_in_executor(None, _call)

    # Try multiple methods to extract text
    response_text = ""

    # Method 1: .text accessor
    try:
        if response.text:
            response_text = response.text
    except (AttributeError, ValueError):
        pass

    # Method 2: dig into parts
    if not response_text:
        try:
            if response.candidates:
                for candidate in response.candidates:
                    if hasattr(candidate, 'content') and candidate.content:
                        if hasattr(candidate.content, 'parts') and candidate.content.parts:
                            for part in candidate.content.parts:
                                if hasattr(part, 'text') and part.text:
                                    response_text += part.text
        except Exception:
            pass

    if not response_text:
        finish = "unknown"
        try:
            finish = str(response.candidates[0].finish_reason) if response.candidates else "no_candidates"
        except Exception:
            pass
        raise Exception(f"Empty SDK response (finish={finish})")

    logger.info(f"[{ean}] Got {len(response_text)} chars from SDK")

    # Extract grounding URLs
    grounding_urls = []
    try:
        if response.candidates and response.candidates[0].grounding_metadata:
            gm = response.candidates[0].grounding_metadata
            if hasattr(gm, 'grounding_chunks') and gm.grounding_chunks:
                for chunk in gm.grounding_chunks:
                    if hasattr(chunk, 'web') and chunk.web and hasattr(chunk.web, 'uri'):
                        grounding_urls.append(chunk.web.uri)
            if hasattr(gm, 'web_search_queries') and gm.web_search_queries:
                logger.info(f"[{ean}] Gemini searched: {gm.web_search_queries}")
    except Exception:
        pass

    return {
        "text": response_text,
        "grounding_urls": grounding_urls
    }


async def call_gemini_for_ean(ean: str, market: str) -> dict:
    """Call Gemini with retries, trying REST API first (more reliable for grounding)."""
    empty_result = {
        "ean": ean, "status": "error",
        "prices": [], "master_data": {"brand": "", "product_name": "", "pack_format": ""}
    }

    if not GEMINI_API_KEY:
        empty_result["error"] = "GEMINI_API_KEY not configured"
        return empty_result

    prompt = build_scrape_prompt(ean, market)

    # Try REST API first (most reliable), then SDK as fallback
    methods = [
        ("REST", lambda: call_gemini_rest(ean, market, prompt)),
    ]
    if HAS_SDK:
        methods.append(("SDK", lambda: call_gemini_sdk(ean, market, prompt)))

    last_error = None
    for method_name, method_fn in methods:
        try:
            raw = await method_fn()
            response_text = raw["text"]
            grounding_urls = raw.get("grounding_urls", [])

            # Parse JSON
            result = parse_gemini_json(response_text)
            result["ean"] = ean

            # Enrich missing source URLs from grounding metadata
            if grounding_urls and result.get("prices"):
                for price_entry in result["prices"]:
                    if not price_entry.get("source_url"):
                        vendor = price_entry.get("vendor_name", "").lower().split()[0] if price_entry.get("vendor_name") else ""
                        for url in grounding_urls:
                            if vendor and vendor in url.lower():
                                price_entry["source_url"] = url
                                break

            price_count = len(result.get("prices", []))
            logger.info(f"[{ean}] ✅ {method_name}: status={result.get('status')}, prices={price_count}")
            return result

        except Exception as e:
            last_error = str(e)
            logger.warning(f"[{ean}] {method_name} failed: {last_error}")
            await asyncio.sleep(1)
            continue

    empty_result["error"] = f"All methods failed. Last: {last_error}"
    logger.error(f"[{ean}] ❌ All methods failed: {last_error}")
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


@app.get("/health")
async def health():
    """Health check — verifies Gemini API key works via REST."""
    if not GEMINI_API_KEY:
        return {"status": "error", "detail": "GEMINI_API_KEY not set"}
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
        payload = {"contents": [{"parts": [{"text": "Reply with exactly: OK"}]}]}
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=payload, params={"key": GEMINI_API_KEY}, timeout=15.0)
            if res.status_code != 200:
                return {"status": "error", "model": GEMINI_MODEL, "http_code": res.status_code, "detail": res.text[:300]}
            data = res.json()
            text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            return {"status": "ok", "model": GEMINI_MODEL, "test_response": text[:50]}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.get("/debug/{ean}")
async def debug_ean(ean: str, market: str = "UK"):
    """Debug endpoint — shows raw Gemini REST response for a single EAN."""
    if not GEMINI_API_KEY:
        return {"error": "No API key"}

    prompt = build_scrape_prompt(ean, market.upper())
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1}
    }

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=payload, params={"key": GEMINI_API_KEY}, timeout=120.0)

        debug_info = {
            "model": GEMINI_MODEL,
            "http_status": res.status_code,
        }

        if res.status_code != 200:
            debug_info["error"] = res.text[:500]
            return debug_info

        data = res.json()
        candidates = data.get("candidates", [])
        debug_info["candidate_count"] = len(candidates)

        if candidates:
            c = candidates[0]
            debug_info["finish_reason"] = c.get("finishReason", "unknown")

            parts = c.get("content", {}).get("parts", [])
            debug_info["parts_count"] = len(parts)

            text_parts = [p["text"] for p in parts if "text" in p]
            full_text = "\n".join(text_parts)
            debug_info["text_length"] = len(full_text)
            debug_info["text_preview"] = full_text[:800] if full_text else None

            gm = c.get("groundingMetadata", {})
            debug_info["search_queries"] = gm.get("webSearchQueries", [])
            debug_info["grounding_chunks"] = len(gm.get("groundingChunks", []))

        return debug_info

    except Exception as e:
        return {"error": str(e)}
