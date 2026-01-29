# frozen_string_literal: true
require "nokogiri"

class PageVerify
  def ean_present?(html, ean)
    txt = text_from(html)
    # exact string match — no inference
    txt.include?(ean)
  end

  def extract_jsonld_blocks(html)
    doc = Nokogiri::HTML(html)
    doc.css('script[type="application/ld+json"]').map(&:text)
  end

  def text_from(html)
    doc = Nokogiri::HTML(html)
    doc.xpath("//script|//style|//noscript").remove
    doc.text.gsub(/\s+/, " ").strip
  end
end
