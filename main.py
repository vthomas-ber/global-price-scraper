import os
import json
import httpx
import re
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright
import google.generativeai as genai

# --- API Keys Configuration ---
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    # Automatically finds the best flash model your key has access to
    available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
    flash_models = [name for name in available_models if 'flash' in name.lower()]
    chosen_model = flash_models[-1] if flash_models else available_models[0]
    model = genai.GenerativeModel(chosen_model)
else:
    model = None

app = FastAPI()

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def read_index():
    return FileResponse("static/index.html")

class ScrapeRequest(BaseModel):
    market: str
    eans: list[str]

# --- SERPAPI SEARCH ENGINES ---
async def run_serpapi_organic(query: str, market_code: str) -> list:
    if not SERPAPI_API_KEY: return []
    url = "https://serpapi.com/search"
    params = {"api_key": SERPAPI_API_KEY, "engine": "google", "q": query, "gl": market_code.lower(), "hl": "en"}
    async with httpx.AsyncClient() as client:
        try:
            res = await client.get(url, params=params, timeout=15.0)
            return res.json().get("organic_results", [])
        except Exception:
            return []

async def run_serpapi_shopping(query: str, market_code: str) -> dict:
    if not SERPAPI_API_KEY: return None
    url = "https://serpapi.com/search"
    params = {"api_key": SERPAPI_API_KEY, "engine": "google_shopping", "q": query, "gl": market_code.lower(), "hl": "en"}
    async with httpx.AsyncClient() as client:
        try:
            res = await client.get(url, params=params, timeout=15.0)
            results = res.json().get("shopping_results", [])
            if results:
                # Grab the top Google Shopping result
                best = results[0] 
                return {
                    "vendor_name": best.get("source", "Google Shopping"),
                    "currency": best.get("currency", ""),
                    "rsv_incl_vat": str(best.get("extracted_price", best.get("price", ""))),
                    "vat_info": "incl. VAT (rate not stated)",
                    "promo_price": "",
                    "price_type_flag": "Google Shopping Match",
                    "pack_format": "",
                    "source_url": best.get("link", "")
                }
        except Exception:
            pass
    return None

# --- PHASE 2.5: Smart EAN Discovery (Name + Weight) ---
async def lookup_ean_name(ean: str, market_code: str) -> str:
    # 1. Open Food Facts (Best for European Groceries)
    off_url = f"https://world.openfoodfacts.org/api/v0/product/{ean}.json"
    async with httpx.AsyncClient() as client:
        try:
            res = await client.get(off_url, timeout=5.0)
            data = res.json()
            if data.get("status") == 1:
                p = data.get("product", {})
                return f"{p.get('brands', '')} {p.get('product_name', '')} {p.get('quantity', '')}".strip()
        except Exception:
            pass

    # 2. SerpApi Organic Snippet Reading (AI Fallback)
    if SERPAPI_API_KEY and model:
        results = await run_serpapi_organic(f'"{ean}"', market_code)
        snippets = [item.get("snippet", "") for item in results[:3]]
        if snippets:
            prompt = f"Based on these search snippets for barcode {ean}, what is the brand, product name, and weight/size? Return ONLY the text (e.g. 'Lassie Haverrijst 250g'). If unknown, return 'UNKNOWN'. Snippets: {snippets}"
            try:
                ai_res = model.generate_content(prompt)
                name = ai_res.text.strip()
                if name and "UNKNOWN" not in name: return name
            except Exception: pass
    return ""

# --- THE BRAIN: Gemini Extraction (FIXED 400 ERROR) ---
def extract_price_data(raw_text: str, ean: str, product_name: str, url: str, is_name_match: bool) -> dict:
    if not model: return {"error": "Gemini API key not configured."}
    match_flag = "[Name Match Only]" if is_name_match else "Regular"
    
    prompt = f"""
    TARGET EAN: {ean}
    DISCOVERED PRODUCT: {product_name if product_name else "Unknown"}
    SOURCE URL: {url}
    
    Extract the pricing data from the raw text below.
    Rules:
    1. Verify EAN match OR strict Double Match (Brand + Weight + Variant).
    2. Extract Regular Selling Value (RSV). If only promo exists, RSV is empty and flag is 'Promo-only'.
    3. Ignore wholesale or 'from' prices.
    4. Provide VAT info as 'incl. VAT (rate shown: X%)' OR 'incl. VAT (rate not stated)'.
    5. Price type flag defaults to '{match_flag}' unless Promo-only or Clearance.
    
    Return ONLY valid JSON. Do not use markdown code blocks. Use exact keys: 
    "vendor_name", "currency", "rsv_incl_vat", "vat_info", "promo_price", "price_type_flag", "pack_format", "source_url".
    If rules aren't met, return {{"error": "No data found"}}.
    
    RAW TEXT:
    {raw_text[:10000]}
    """
    try:
        # NO generation_config used here to avoid the 400 error!
        response = model.generate_content(prompt)
        raw_text = response.text.strip()
        
        # Smart regex parser to handle LLM markdown habits
        if "```json" in raw_text: 
            raw_text = raw_text.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_text: 
            raw_text = raw_text.split("```")[1].split("```")[0].strip()
            
        return json.loads(raw_text)
    except Exception as e:
        return {"error": f"Failed to parse LLM response"}

# --- MAIN ORCHESTRATOR ---
@app.post("/scrape")
async def run_scraper(request: ScrapeRequest):
    if len(request.eans) > 10: raise HTTPException(status_code=400, detail="Max 10 EANs")
    final_results = {}
    
    golden_sites = {
        "FR": "(site:carrefour.fr OR site:auchan.fr OR site:coursesu.com OR site:intermarche.com OR site:monoprix.fr OR site:franprix.fr)",
        "UK": "(site:tesco.com OR site:sainsburys.co.uk OR site:asda.com OR site:morrisons.com OR site:iceland.co.uk OR site:waitrose.com)",
        "NL": "(site:ah.nl OR site:jumbo.com OR site:plus.nl OR site:dirk.nl OR site:vomar.nl)",
        "BE": "(site:delhaize.be OR site:colruyt.be OR site:carrefour.be OR site:ah.be)",
        "DE": "(site:rewe.de OR site:edeka.de OR site:kaufland.de OR site:dm.de OR site:rossmann.de)",
        "DK": "(site:nemlig.com OR site:bilkatogo.dk OR site:rema1000.dk OR site:netto.dk)",
        "IT": "(site:carrefour.it OR site:conad.it OR site:esselunga.it OR site:coop.it)",
        "ES": "(site:carrefour.es OR site:mercadona.es OR site:dia.es OR site:alcampo.es)",
        "SE": "(site:ica.se OR site:coop.se OR site:willys.se OR site:hemkop.se)",
        "NO": "(site:oda.com OR site:meny.no OR site:spar.no)",
        "PL": "(site:carrefour.pl OR site:auchan.pl OR site:biedronka.pl)",
        "PT": "(site:continente.pt OR site:auchan.pt OR site:pingo-doce.pt)"
    }
    sites_query = golden_sites.get(request.market.upper(), "")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for ean in request.eans:
            extracted_data = None
            product_name = await lookup_ean_name(ean, request.market)
            
            # --- STRATEGY 1: SerpApi Google Shopping (Bypasses AH/Jumbo Bot Blocks!) ---
            shopping_data = await run_serpapi_shopping(ean, request.market)
            if not shopping_data and product_name:
                shopping_data = await run_serpapi_shopping(product_name, request.market)
            
            if shopping_data:
                final_results[ean] = shopping_data
                continue

            # --- STRATEGY 2: SerpApi Organic + Playwright Fallback ---
            is_name_match = False
            org_results = await run_serpapi_organic(f'"{ean}" {sites_query}', request.market)
            
            if not org_results and product_name:
                is_name_match = True
                org_results = await run_serpapi_organic(f'"{product_name}" {sites_query}', request.market)
            
            if not org_results:
                final_results[ean] = {"status": "No data found", "error": f"No data found. (Name: {product_name or 'Unknown'})", "source_url": ""}
                continue
                
            target_url = org_results[0].get("link", "")
            
            # Scrape with Playwright
            page_text = ""
            page = await browser.new_page()
            try:
                await page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media", "font", "stylesheet"] else route.continue_())
                await page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                page_text = await page.evaluate("document.body.innerText")
            except Exception:
                pass
            finally:
                await page.close()
            
            if not page_text:
                final_results[ean] = {"status": "Scraping failed", "source_url": target_url}
                continue
                
            # Extract with Gemini
            extracted_data = extract_price_data(page_text, ean, product_name, target_url, is_name_match)
            if "source_url" not in extracted_data:
                extracted_data["source_url"] = target_url
            
            final_results[ean] = extracted_data
            
        await browser.close()
        
    return {"market": request.market, "results": final_results}