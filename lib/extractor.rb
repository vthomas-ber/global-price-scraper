# frozen_string_literal: true
require_relative "./page_verify"
require_relative "./gemini_client"

class Extractor
  def initialize
    @verify = PageVerify.new
    @gemini = GeminiClient.new
  end

  def extract(market:, ean:, vendor:, url:, html:)
    text = @verify.text_from(html)

    unless @verify.ean_present?(text, ean) || @verify.extract_jsonld_blocks(html).any? { |blk| blk.include?(ean) }
      return {
        ok: false,
        discard_reason: "EAN not explicitly present in HTML/text/json-ld"
      }
    end

    out = @gemini.extract_price_from_text(
      market: market, ean: ean, vendor: vendor, url: url, text: text
    )

    valid = out["valid"] == true
    return { ok: false, discard_reason: "Gemini marked invalid" } unless valid

    {
      ok: true,
      row: {
        vendor: vendor,
        market: market,
        currency: out["currency"],
        rsv: out["rsv"],
        vat_info: out["vat_info"],
        promo_price: out["promo_price"],
        flag: out["flag"] || "No-data",
        pack_format: out["pack_format"],
        ean_evidence: "EAN verified in page text",
        url: url
      }
    }
  end
end
