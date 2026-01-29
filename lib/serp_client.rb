# frozen_string_literal: true
require "httparty"
require "addressable/uri"

class SerpClient
  ENDPOINT = "https://serpapi.com/search.json"

  def initialize(api_key: ENV["SERPAPI_KEY"])
    @api_key = api_key
    raise "Missing SERPAPI_KEY" if @api_key.to_s.empty?
  end

  def discover_urls(market:, ean:, domains:, max_results: 8)
    q = %(#{ean} (EAN OR GTIN) site:#{domains.join(" OR site:")})
    params = {
      engine: "google",
      q: q,
      hl: "en",
      gl: market.downcase,
      num: 10,
      api_key: @api_key
    }
    resp = HTTParty.get(ENDPOINT, query: params, timeout: 25)
    raise "SERPAPI error #{resp.code}" unless resp.code == 200

    items = (resp.parsed_response["organic_results"] || [])
    urls = items.map { |it| it["link"] }.compact

    # normalize + de-dupe
    urls = urls.map { |u| Addressable::URI.parse(u).normalize.to_s rescue nil }.compact
    urls.uniq.take(max_results)
  end
end
