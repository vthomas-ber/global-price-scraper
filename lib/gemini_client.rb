# frozen_string_literal: true
require "httparty"
require "json"

class GeminiClient
  def initialize(api_key: ENV["GEMINI_API_KEY"])
    @api_key = api_key
    raise "Missing GEMINI_API_KEY" if @api_key.to_s.empty?
  end

  def extract_price_from_text(market:, ean:, vendor:, url:, text:)
    prompt = {
      "task" => "Extract price data from the provided retailer page text.",
      "hard_rules" => [
        "Return JSON only, no prose.",
        "Do NOT guess. If uncertain, return null.",
        "EAN must be explicitly present in the provided text; if not, return {valid:false}.",
        "Return the non-promotional FULL price (RSV) if shown; if only promo exists, set rsv=null and promo_price=the promo.",
        "Do NOT derive per-unit from multipack unless pack_count is explicit."
      ],
      "inputs" => {
        "market" => market,
        "ean" => ean,
        "vendor" => vendor,
        "url" => url,
        "page_text" => text[0, 12000] # keep it bounded
      },
      "output_schema" => {
        "valid" => "boolean",
        "currency" => "string|null",
        "rsv" => "number|null",
        "vat_info" => "string|null",
        "promo_price" => "number|null",
        "pack_format" => "string|null",
        "flag" => "Comparable|Promo-only|Non-comparable|No-data",
        "evidence_snippet" => "string|null"
      }
    }

    endpoint = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key=#{@api_key}"

    body = {
      contents: [{
        parts: [{ text: JSON.pretty_generate(prompt) }]
      }],
      generationConfig: {
        temperature: 0.0,
        responseMimeType: "application/json"
      }
    }

    resp = HTTParty.post(endpoint, body: body.to_json, headers: { "Content-Type" => "application/json" }, timeout: 35)
    raise "Gemini HTTP #{resp.code}" unless resp.code == 200

    raw = resp.parsed_response.dig("candidates", 0, "content", "parts", 0, "text")
    JSON.parse(raw)
  rescue => e
    { "valid" => false, "flag" => "No-data", "rsv" => nil, "promo_price" => nil, "currency" => nil, "vat_info" => nil, "pack_format" => nil, "evidence_snippet" => "Gemini error: #{e.message}" }
  end
end
