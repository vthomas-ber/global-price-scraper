# frozen_string_literal: true
require "httparty"

class EanSearchClient
  def initialize(api_key: ENV["EANSEARCH_API_KEY"])
    @api_key = api_key
  end

  def lookup(ean)
    return nil if @api_key.to_s.empty?
    # NOTE: adapt to your ean-search.org API plan/endpoint.
    # This is a placeholder pattern: you must update the URL to your plan's endpoint.
    url = "https://api.ean-search.org/api"
    resp = HTTParty.get(url, query: { op: "barcode-lookup", barcode: ean, format: "json", key: @api_key }, timeout: 25)
    return nil unless resp.code == 200
    resp.parsed_response
  rescue
    nil
  end
end
