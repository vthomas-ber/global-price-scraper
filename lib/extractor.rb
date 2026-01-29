# frozen_string_literal: true
require "uri"
require_relative "./rules"

class Extractor
  MAX_ROWS_PER_EAN = 6

  def scrape_urls(market:, ean:, urls:, fetcher:)
    rows = []
    discards = []
    master = { "product_name" => nil, "unit_type" => "Single Unit", "grammage" => nil }

    urls.each do |url|
      break if rows.size >= MAX_ROWS_PER_EAN

      begin
        page = fetcher.fetch(url)
        html = page["html"].to_s
        text = page["text"].to_s
        final_url = page["finalUrl"] || url

        unless ean_present?(ean, html, text)
          discards << { "url" => final_url, "reason" => "EAN not explicitly verifiable on page/source" }
          next
        end

        price, evidence = extract_visible_price_with_evidence(text)
        if price.nil?
          discards << { "url" => final_url, "reason" => "Visible consumer price not confidently extractable (discarded)" }
          next
        end

        if Rules.forbidden_context?(evidence)
          discards << { "url" => final_url, "reason" => "Promo/multi-buy context detected (non-comparable, discarded)" }
          next
        end

        vendor = vendor_from_url(final_url)
        master["product_name"] ||= guess_product_name(text)

        rows << {
          "grocery_vendor_name" => vendor,
          "market" => market,
          "currency" => currency_for_market(market),
          "rsv" => price,
          "vat_info" => "incl. VAT (rate not stated)",
          "promo_price" => nil,
          "price_type_flag" => "Regular",
          "pack_format" => master["grammage"], # optional
          "calculated_per_unit_rsv" => nil,
          "source_url" => final_url,
          "price_evidence" => evidence,
          "evidence_source" => "visible page text",
          "comparable" => true
        }
      rescue StandardError => e
        discards <<({ "url" => url, "reason" => "Fetch/extract error: #{e.message}" })
      end
    end

    [rows, discards, master]
  end

  private

  def currency_for_market(market)
    # Extend later; MVP assumes EUR for DE
    return "GBP" if market == "UK"
    return "EUR" # DE/NL/BE/FR/...
  end

  def vendor_from_url(url)
    host = URI(url).host.to_s.sub(/^www\./, "")
    # Keep it readable: "kaufland.de" => "Kaufland"
    base = host.split(".").first
    base.nil? || base.empty? ? "Unknown" : base.capitalize
  rescue StandardError
    "Unknown"
  end

  def ean_present?(ean, html, text)
    # Your strongest invariant: 100% EAN visibility in page or source
    text.include?(ean) || html.include?(ean)
  end

  # Extract visible EUR-style "2,68 €" or "2.68 €" from visible text.
  # Returns [Float price, String evidence_snippet]
  def extract_visible_price_with_evidence(text)
    lines = text.split("\n").map(&:strip).reject(&:empty?)

    # Consider only lines with € and short enough to be "price context"
    euro_lines = lines.select { |l| l.include?("€") && l.length <= 120 }
    return [nil, nil] if euro_lines.empty?

    candidates = []

    euro_lines.each do |l|
      # Reject unit prices like "/kg", "/100 g"
      next if l.match?(/\/\s?(kg|g|100\s*g|l|ml)/i)

      # Capture 1.80 €, 1,80 €, etc.
      if (m = l.match(/(\d{1,3}[.,]\d{2})\s*€/))
        val = m[1].tr(",", ".").to_f
        candidates << [val, l]
      end
    end

    return [nil, nil] if candidates.empty?

    # Pick most "standalone" price line: shortest line tends to be the main price widget
    best = candidates.min_by { |_val, line| line.length }
    [best[0], best[1][0, 90]]
  end

  def guess_product_name(text)
    # Conservative: look for a likely title-ish line near the top
    lines = text.split("\n").map(&:strip).reject(&:empty?)
    lines.find { |l| l.length.between?(10, 80) }&.slice(0, 80)
  end
end
