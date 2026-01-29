# frozen_string_literal: true
require "httparty"

class HttpFetcher
  def fetch(url)
    resp = HTTParty.get(url, timeout: 25, headers: {
      "User-Agent" => "Mozilla/5.0 (compatible; GlobalPriceScraper/1.0)"
    })
    raise "HTTP #{resp.code}" unless resp.code == 200
    resp.body.to_s
  end
end
