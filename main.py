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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    
    # --- BULLETPROOF MODEL SELECTOR ---
    # Asks Google's servers what models your specific key has access to
    available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
    flash_models = [name for name in available_models if 'flash' in name.lower()]
    
    # Pick the newest flash model available (or fallback to the first valid one)
    chosen_model = flash_models[-1] if flash_models else available_models[0]
    print(f"Automatically selected model: {chosen_model}")
    
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

# --- PHASE 2.5: Smart EAN Discovery (The Game Changer) ---
async def lookup_ean_name(ean: str, market_code: str) -> str:
    # 1. Try Open Food Facts (Free, highly accurate for EU groceries)
    off_url = f"https://world.openfoodfacts.org/api/v0/product/{ean}.json"
    async with httpx.AsyncClient() as client:
        try:
            res = await client.get(off_url, timeout=5.0)
            data = res.json()
            if data.get("status") == 1:
                p = data.get("product", {})
                return f"{p.get('brands', '')} {p.get('product_name', '')}".strip()
        except Exception:
            pass

    # 2. Smart AI Fallback (Mimics native Gemini behavior)
    if SERPER_API_KEY and model:
        url = "https://google.serper.dev/search"
        payload = json.dumps({"q": f'"{ean}"', "gl": market_code.lower()})
        headers = {'X-API-KEY': SERPER_API_KEY, 'Content-Type': 'application/json'}
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, headers=headers, data=payload)
                results = response.json()
                # Grab the text snippets from the top 3 Google results
                snippets = [item.get("snippet", "") for item in results.get("organic", [])][:3]
                if snippets:
                    prompt = f"Based on these Google search snippets for barcode {ean}, what is the brand and product name? Return ONLY the brand and name (e.g. 'Lassie Haverrijst'). If unknown, return 'UNKNOWN'. Snippets: {snippets}"
                    ai_res = model.generate_content(prompt)
                    name = ai_res.text.strip()
                    if name and "UNKNOWN" not in name:
                        return name
            except Exception:
                pass
                
    return ""

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

# --- MAIN ORCHESTRATOR ---
@app.post("/scrape")
async def run_scraper(request: ScrapeRequest):
    if len(request.eans) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 EANs allowed per request.")
        
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
            # PHASE 1: Try finding explicit EAN on Golden Sites
            urls = await run_serper_search(f'"{ean}" {sites_query}', request.market)
            
            product_name = ""
            is_name_match = False
            
            # PHASE 2.5 & 4: If EAN is hidden, discover the name and search Golden Sites by name
            if not urls:
                product_name = await lookup_ean_name(ean, request.market)
                if product_name:
                    is_name_match = True
                    urls = await run_serper_search(f'"{product_name}" {sites_query}', request.market)
            
            # PHASE 3: If Name search also fails, do a broad fallback
            if not urls:
                is_name_match = False 
                urls = await run_serper_search(f'"{ean}" grocery OR supermarket', request.market)
                
            if not urls:
                final_results[ean] = {"status": "No data found", "error": f"No data found. (Discovered Name: {product_name or 'None'})", "source_url": ""}
                continue
                
            target_url = urls[0]
            
            # SCRAPE
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
                
            # EXTRACT
            extracted_data = extract_price_data(page_text, ean, product_name, target_url, is_name_match)
            extracted_data["source_url"] = target_url
            
            final_results[ean] = extracted_data
            
        await browser.close()
        
    return {"market": request.market, "results": final_results}