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

# Mount static files for the frontend
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def read_index():
    return FileResponse("static/index.html")

class ScrapeRequest(BaseModel):
    market: str
    eans: list[str]

# --- 1. The "Eyes": Serper API (Phase 1 & 2) ---
async def search_for_product_urls(ean: str, market_code: str) -> list[str]:
    if not SERPER_API_KEY:
        return []
    
    url = "https://google.serper.dev/search"
    # Mapping market to golden websites (Example mapping)
    golden_sites = {
        "UK": "site:tesco.com OR site:sainsburys.co.uk OR site:asda.com OR site:morrisons.com",
        "FR": "site:carrefour.fr OR site:auchan.fr OR site:coursesu.com",
        "DE": "site:rewe.de OR site:edeka.de OR site:kaufland.de"
    }
    
    sites_query = golden_sites.get(market_code.upper(), "")
    query = f'"{ean}" {sites_query}'.strip()
    
    payload = json.dumps({
      "q": query,
      "gl": market_code.lower() 
    })
    headers = {
      'X-API-KEY': SERPER_API_KEY,
      'Content-Type': 'application/json'
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, data=payload)
            results = response.json()
            urls = [item["link"] for item in results.get("organic", [])]
            return urls
        except Exception as e:
            print(f"Serper search failed: {e}")
            return []

# --- 2. The Scraper: Playwright ---
async def scrape_page_text(url: str) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            # Block resources to speed up scraping
            await page.route("**/*", lambda route: route.abort() 
                if route.request.resource_type in ["image", "media", "font", "stylesheet"] 
                else route.continue_())
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            text_content = await page.evaluate("document.body.innerText")
            return text_content
        except Exception as e:
            print(f"Scraping failed for {url}: {e}")
            return ""
        finally:
            await browser.close()

# --- 3. The "Brain": Gemini Extraction ---
def extract_price_data(raw_text: str, ean: str, url: str) -> dict:
    if not model:
        return {"error": "Gemini API key not configured."}
        
    prompt = f"""
    You are an expert retail data extractor following strict rules for Ambient Food items.
    
    TARGET EAN: {ean}
    SOURCE URL: {url}
    
    Examine the raw text below and extract the Regular Selling Value (RSV) consumer price.
    Rules:
    1. Verify explicit EAN match or strict Double Match (Brand + Weight/Size + Name/Variant).
    2. Extract non-promotional price. If only promo exists, mark RSV empty and flag 'Promo-only'.
    3. Ignore wholesale or 'from' prices.
    4. Provide VAT info as 'incl. VAT (rate shown: X%)' OR 'incl. VAT (rate not stated)'.
    
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

# --- 4. Main Endpoint Orchestration ---
@app.post("/scrape")
async def run_scraper(request: ScrapeRequest):
    if len(request.eans) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 EANs allowed per request.")
        
    final_results = {}
    
    for ean in request.eans:
        urls = await search_for_product_urls(ean, request.market)
        
        if not urls:
            final_results[ean] = {"status": "No data found", "error": "No URLs found via search.", "source_url": ""}
            continue
            
        target_url = urls[0]
        page_text = await scrape_page_text(target_url)
        
        if not page_text:
            final_results[ean] = {"status": "Scraping failed or blocked", "source_url": target_url}
            continue
            
        extracted_data = extract_price_data(page_text, ean, target_url)
        extracted_data["source_url"] = target_url
        
        final_results[ean] = extracted_data
        
    return {"market": request.market, "results": final_results}
