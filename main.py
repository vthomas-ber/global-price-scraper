import os
import json
import asyncio
import logging
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from google import genai
from google.genai import types

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("price-scraper")

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


def extract_text_from_response(response) -> str:
    """Extract text from Gemini response, handling all possible structures."""
    # Method 1: Direct .text accessor
    try:
        if response.text:
            return response.text
    except (AttributeError, ValueError):
        pass

    # Method 2: Dig into candidates → content → parts
    try:
        if response.candidates:
            for candidate in response.candidates:
                if hasattr(candidate, 'content') and candidate.content:
                    if hasattr(candidate.content, 'parts') and candidate.content.parts:
                        texts = []
                        for part in candidate.content.parts:
                            if hasattr(part, 'text') and part.text:
                                texts.append(part.text)
                        if texts:
                            return "\n".join(texts)
    except (AttributeError, IndexError):
        pass

    # Method 3: Try to serialize the whole response
    try:
        raw = str(response)
        if "{" in raw and "}" in raw:
            return raw
    except Exception:
        pass

    return ""


async def call_gemini_for_ean(ean: str, market: str, max_retries: int = 2) -> dict:
    """Call Gemini API with Google Search grounding to scrape price for one EAN."""
    empty_result = {
        "ean": ean, "status": "error",
        "prices": [], "master_data": {"brand": "", "product_name": "", "pack_format": ""}
    }

    if not GEMINI_API_KEY:
        empty_result["error"] = "GEMINI_API_KEY not configured"
        return empty_result

    prompt = build_scrape_prompt(ean, market)
    client = genai.Client(api_key=GEMINI_API_KEY)

    for attempt in range(max_retries + 1):
        try:
            # First attempt: with Google Search grounding
            # On retry: try without grounding (sometimes grounding causes empty responses)
            if attempt == 0:
                config = types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    temperature=0.1,
                )
                logger.info(f"[{ean}] Attempt {attempt + 1}: with Google Search grounding")
            else:
                config = types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    temperature=0.2 + (attempt * 0.1),
                )
                logger.info(f"[{ean}] Attempt {attempt + 1}: retry with temp={0.2 + (attempt * 0.1)}")

            def _call():
                return client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                    config=config,
                )

            response = await asyncio.get_event_loop().run_in_executor(None, _call)

            # Extract text using robust method
            response_text = extract_text_from_response(response)

            if not response_text:
                logger.warning(f"[{ean}] Attempt {attempt + 1}: Empty response text")
                # Log what we did get for debugging
                try:
                    if response.candidates:
                        c = response.candidates[0]
                        logger.warning(f"[{ean}] finish_reason: {c.finish_reason}")
                        if hasattr(c, 'grounding_metadata') and c.grounding_metadata:
                            gm = c.grounding_metadata
                            if hasattr(gm, 'web_search_queries') and gm.web_search_queries:
                                logger.info(f"[{ean}] Search queries executed: {gm.web_search_queries}")
                except Exception as e:
                    logger.warning(f"[{ean}] Could not inspect response: {e}")

                if attempt < max_retries:
                    await asyncio.sleep(1 + attempt)  # Brief backoff
                    continue
                else:
                    empty_result["error"] = "Empty response from Gemini after all retries"
                    return empty_result

            logger.info(f"[{ean}] Got response text ({len(response_text)} chars)")
            logger.debug(f"[{ean}] Response preview: {response_text[:200]}")

            # Parse JSON
            result = parse_gemini_json(response_text)
            result["ean"] = ean

            # Enrich source URLs from grounding metadata
            grounding_urls = []
            try:
                if response.candidates and response.candidates[0].grounding_metadata:
                    gm = response.candidates[0].grounding_metadata
                    if hasattr(gm, 'grounding_chunks') and gm.grounding_chunks:
                        for chunk in gm.grounding_chunks:
                            if hasattr(chunk, 'web') and chunk.web and hasattr(chunk.web, 'uri'):
                                grounding_urls.append(chunk.web.uri)
                    if hasattr(gm, 'web_search_queries') and gm.web_search_queries:
                        logger.info(f"[{ean}] Grounding searches: {gm.web_search_queries}")
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

            price_count = len(result.get("prices", []))
            logger.info(f"[{ean}] Success: status={result.get('status')}, prices={price_count}")
            return result

        except json.JSONDecodeError as e:
            logger.error(f"[{ean}] Attempt {attempt + 1}: JSON parse error: {e}")
            if attempt < max_retries:
                await asyncio.sleep(1)
                continue
            empty_result["error"] = f"JSON parse error: {str(e)}"
            return empty_result

        except Exception as e:
            logger.error(f"[{ean}] Attempt {attempt + 1}: Error: {e}")
            if attempt < max_retries:
                await asyncio.sleep(1)
                continue
            empty_result["error"] = str(e)
            return empty_result

    empty_result["error"] = "All retries exhausted"
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
    """Health check — also verifies Gemini API key works."""
    if not GEMINI_API_KEY:
        return {"status": "error", "detail": "GEMINI_API_KEY not set"}
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents="Reply with exactly: OK",
            config=types.GenerateContentConfig(temperature=0),
        )
        return {
            "status": "ok",
            "model": GEMINI_MODEL,
            "test_response": extract_text_from_response(response)[:50]
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.get("/debug/{ean}")
async def debug_ean(ean: str, market: str = "UK"):
    """Debug endpoint — shows raw Gemini response for a single EAN."""
    if not GEMINI_API_KEY:
        return {"error": "No API key"}

    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = build_scrape_prompt(ean, market.upper())

    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.1,
    )

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL, contents=prompt, config=config
        )

        # Gather everything we can about the response
        debug_info = {
            "model": GEMINI_MODEL,
            "text_via_accessor": None,
            "text_via_parts": None,
            "finish_reason": None,
            "grounding_queries": None,
            "grounding_chunk_count": 0,
        }

        try:
            debug_info["text_via_accessor"] = response.text[:500] if response.text else None
        except Exception as e:
            debug_info["text_via_accessor_error"] = str(e)

        try:
            if response.candidates:
                c = response.candidates[0]
                debug_info["finish_reason"] = str(c.finish_reason)
                if c.content and c.content.parts:
                    parts_text = [p.text for p in c.content.parts if hasattr(p, 'text') and p.text]
                    debug_info["text_via_parts"] = parts_text[0][:500] if parts_text else None
                    debug_info["parts_count"] = len(c.content.parts)
                if c.grounding_metadata:
                    gm = c.grounding_metadata
                    if hasattr(gm, 'web_search_queries'):
                        debug_info["grounding_queries"] = gm.web_search_queries
                    if hasattr(gm, 'grounding_chunks') and gm.grounding_chunks:
                        debug_info["grounding_chunk_count"] = len(gm.grounding_chunks)
        except Exception as e:
            debug_info["inspection_error"] = str(e)

        # Also try full extraction
        full_text = extract_text_from_response(response)
        debug_info["extracted_text_length"] = len(full_text) if full_text else 0
        debug_info["extracted_text_preview"] = full_text[:500] if full_text else None

        return debug_info

    except Exception as e:
        return {"error": str(e)}