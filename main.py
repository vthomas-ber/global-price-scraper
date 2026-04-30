import os
import json
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright
import google.generativeai as genai

# --- API Keys Configuration ---
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
EAN_SEARCH_API_KEY = os.getenv("EAN_SEARCH_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
else:
    model = None

app = FastAPI()

# Bulletproof folder creation
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def read_index():
    return FileResponse("static/index.html")

class ScrapeRequest(BaseModel):
    market: str
    eans: list[str]

# --- HELPER: Serper API Search ---
async def run_serper_search(query: str, market_code: str) -> list[str]:
    if not SERPER_API_KEY:
        return []
    url = "https://google.serper.dev/search"
    payload = json.dumps({"q": query, "gl": market_code.lower()})
    headers = {'X-API-KEY': SERPER_API_KEY, 'Content-Type': 'application/json'}
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, data=payload)
            results = response.json()
            return [item["link"] for item in results.get("organic", [])]
        except Exception:
            return []

# --- PHASE 2.5: EAN-Search API Lookup ---
async def lookup_ean_name(ean: str) -> str:
    if not EAN_SEARCH_API_KEY:
        return ""
    # Using ean-search.org API format. 
    url = f"https://api.ean-search.org/api?token={EAN_SEARCH_API_KEY}&op=barcode-lookup&ean={ean}"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url)
            data = response.json()
            if isinstance(data, list) and len(data) > 0 and "name" in data[0]:
                return data[0]["name"]
            return ""
        except Exception:
            return ""

# --- THE SCRAPER: Playwright ---
async def scrape_page_text(url: str) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.route("**/*", lambda route: route.abort() 
                if route.request.resource_type in ["image", "media", "font", "stylesheet"] 
                else route.continue_())
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            return await page.evaluate("document.body.innerText")
        except Exception:
            return ""
        finally:
            await browser.close()

# --- THE BRAIN: Gemini Extraction ---
def extract_price_data(raw_text: str, ean: str, product_name: str, url: str, is_name_match: bool) -> dict:
    if not model:
        return {"error": "Gemini API key not configured."}
        
    match_flag = "[Name Match Only]" if is_name_match else "Regular"
    
    prompt = f"""
    You are an expert retail data extractor for Ambient Food items.
    
    TARGET EAN: {ean}
    DISCOVERED PRODUCT NAME: {product_name if product_name else "Unknown"}
    SOURCE URL: {url}
    
    Examine the raw text below and extract the pricing data.
    Rules:
    1. Verify explicit EAN match OR strict Double Match (Brand + Weight + Variant) using the Discovered Product Name.
    2. Extract non-promotional Regular Selling Value (RSV). If only promo exists, RSV is empty and flag is 'Promo-only'.
    3. Ignore wholesale or 'from' prices.
    4. Provide VAT info as 'incl. VAT (rate shown: X%)' OR 'incl. VAT (rate not stated)'.
    5. Price type flag should default to '{match_flag}' unless it is Promo-only or Clearance.
    
    Return ONLY JSON with these exact keys: 
    "vendor_name", "currency", "rsv_incl_vat", "vat_info", "promo_price", "price_type_flag", "pack_format", "per_unit_rsv".
    If the product is not found or rules aren't met, return {{"error": "No data found"}}.
    
    RAW TEXT (Truncated):
    {raw_text[:10000]}
    """
    try:
        response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(response.text)
    except Exception as e:
        return {"error": f"Failed to parse LLM response: {str(e)}"}

# --- MAIN ORCHESTRATOR (PHASES 1 -> 4) ---
@app.post("/scrape")
async def run_scraper(request: ScrapeRequest):
    if len(request.eans) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 EANs allowed per request.")
        
    final_results = {}
    
    # Added NL to the Golden Sites dictionary
    golden_sites = {
        "UK": "(site:tesco.com OR site:sainsburys.co.uk OR site:asda.com OR site:morrisons.com OR site:iceland.co.uk OR site:waitrose.com)",
        "FR": "(site:carrefour.fr OR site:auchan.fr OR site:coursesu.com OR site:intermarche.com)",
        "DE": "(site:rewe.de OR site:edeka.de OR site:kaufland.de)",
        "NL": "(site:ah.nl OR site:jumbo.com OR site:plus.nl OR site:dirk.nl OR site:vomar.nl)"
    }
    
    sites_query = golden_sites.get(request.market.upper(), "")

    # OPTIMIZATION: Launch browser ONCE for the entire batch to prevent Uvicorn timeout
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        for ean in request.eans:
            # PHASE 1 & 2: Search by EAN
            urls = await run_serper_search(f'"{ean}" {sites_query}', request.market)
            if not urls:
                urls = await run_serper_search(f'"{ean}" grocery OR supermarket', request.market)
            
            product_name = ""
            is_name_match = False
            
            # PHASE 2.5 & 4: Lookup Name and search by Name
            if not urls:
                product_name = await lookup_ean_name(ean)
                if product_name:
                    is_name_match = True
                    urls = await run_serper_search(f'"{product_name}" {sites_query}', request.market)
            
            # If STILL no URLs
            if not urls:
                final_results[ean] = {"status": "No data found", "error": "No URLs found after EAN and Name search.", "source_url": ""}
                continue
                
            target_url = urls[0]
            
            # SCRAPE using the shared browser
            page_text = ""
            page = await browser.new_page()
            try:
                await page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media", "font", "stylesheet"] else route.continue_())
                await page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                page_text = await page.evaluate("document.body.innerText")
            except Exception:
                page_text = ""
            finally:
                await page.close()
            
            if not page_text:
                final_results[ean] = {"status": "Scraping failed or blocked", "source_url": target_url}
                continue
                
            # Extract data using Gemini
            extracted_data = extract_price_data(page_text, ean, product_name, target_url, is_name_match)
            extracted_data["source_url"] = target_url
            
            final_results[ean] = extracted_data
            
        await browser.close()
        
    return {"market": request.market, "results": final_results}

# --- MAIN ORCHESTRATOR (PHASES 1 -> 4) ---
@app.post("/scrape")
async def run_scraper(request: ScrapeRequest):
    if len(request.eans) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 EANs allowed per request.")
        
    final_results = {}
    
# ALL markets from the original prompt, properly formatted for Serper
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
    
    for ean in request.eans:
        sites_query = golden_sites.get(request.market.upper(), "")
        
        # PHASE 1 & 2: Search by EAN
        urls = await run_serper_search(f'"{ean}" {sites_query}', request.market)
        if not urls:
            urls = await run_serper_search(f'"{ean}" grocery OR supermarket', request.market)
        
        product_name = ""
        is_name_match = False
        
        # PHASE 2.5 & 4: If no URLs, lookup Name and search by Name
        if not urls:
            product_name = await lookup_ean_name(ean)
            if product_name:
                is_name_match = True
                urls = await run_serper_search(f'"{product_name}" {sites_query}', request.market)
        
        # If STILL no URLs after all phases
        if not urls:
            final_results[ean] = {"status": "No data found", "error": "No URLs found after EAN and Name search.", "source_url": ""}
            continue
            
        # SCRAPE & EXTRACT (Taking the top URL for now)
        target_url = urls[0]
        page_text = await scrape_page_text(target_url)
        
        if not page_text:
            final_results[ean] = {"status": "Scraping failed or blocked", "source_url": target_url}
            continue
            
        extracted_data = extract_price_data(page_text, ean, product_name, target_url, is_name_match)
        extracted_data["source_url"] = target_url
        
        final_results[ean] = extracted_data
        
    return {"market": request.market, "results": final_results}