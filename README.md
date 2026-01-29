# Global Price Scraper

EAN-based grocery price scraper (baseline) using:
- SERPAPI for discovery
- Plain HTTP fetch
- Strict EAN presence verification
- Gemini for structured extraction (no guessing)

## Env vars
- SERPAPI_KEY
- GEMINI_API_KEY
- EANSEARCH_API_KEY (optional)

## Run locally
```bash
bundle install
bundle exec rackup -o 0.0.0.0 -p 3000
