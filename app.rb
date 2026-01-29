# frozen_string_literal: true

require "sinatra"
require "json"
require "dotenv/load"

require_relative "./lib/serp"
require_relative "./lib/playwright_fetcher"
require_relative "./lib/extractor"
require_relative "./lib/rules"
require_relative "./lib/cache"

# ------------------------------------------------------------
# Basic server config
# ------------------------------------------------------------
set :bind, "0.0.0.0"
set :port, ENV.fetch("PORT", "3000").to_i
set :environment, ENV.fetch("RACK_ENV", "development").to_sym

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
helpers do
  def json_body
    request.body.rewind
    raw = request.body.read
    raw.empty? ? {} : JSON.parse(raw)
  rescue JSON::ParserError
    halt 400, { error: "Invalid JSON" }.to_json
  end
end

# ------------------------------------------------------------
# DEBUG: runtime inspection (Step A)
# ------------------------------------------------------------
get "/__debug_runtime" do
  content_type :json

  node_path = `which node 2>/dev/null`.strip
  npm_path  = `which npm 2>/dev/null`.strip

  {
    ruby_version: RUBY_VERSION,
    rack_env: ENV["RACK_ENV"],
    node_path: node_path.empty? ? nil : node_path,
    node_version: node_path.empty? ? nil : `node -v 2>/dev/null`.strip,
    npm_path: npm_path.empty? ? nil : npm_path,
    npm_version: npm_path.empty? ? nil : `npm -v 2>/dev/null`.strip
  }.to_json
end

# ------------------------------------------------------------
# DEBUG: host inspection (already useful, keep it)
# ------------------------------------------------------------
get "/__debug_host" do
  content_type :json
  {
    host: request.host,
    http_host: request.env["HTTP_HOST"],
    forwarded_host: request.env["HTTP_X_FORWARDED_HOST"],
    forwarded_proto: request.env["HTTP_X_FORWARDED_PROTO"]
  }.to_json
end

# ------------------------------------------------------------
# UI
# ------------------------------------------------------------
get "/" do
  erb :index
end

# ------------------------------------------------------------
# Main scrape endpoint
# ------------------------------------------------------------
post "/scrape" do
  content_type :json
  body = json_body

  market = (body["market"] || "").strip.upcase
  eans = Array(body["eans"]).map(&:to_s).map(&:strip).reject(&:empty?)

  halt 400, { error: "market is required" }.to_json if market.empty?
  halt 400, { error: "Provide 1–10 EANs" }.to_json if eans.empty? || eans.size > 10

  cache = Cache.new
  serp = Serp.new
  fetcher = PlaywrightFetcher.new
  extractor = Extractor.new

  results = eans.map do |ean|
    Rules.validate_ean!(ean)

    # 🚨 cache key bumped to v2 to avoid frozen bad results
    cache_key = "scrape:v2:#{market}:#{ean}"
    cached = cache.get(cache_key)
    next cached.merge("cached" => true) if cached

    urls = serp.discover_urls(market: market, ean: ean)

    rows, discards, master = extractor.scrape_urls(
      market: market,
      ean: ean,
      urls: urls,
      fetcher: fetcher
    )

    out = {
      "ean" => ean,
      "master" => master,
      "rows" => rows,
      "average_rsv" => Rules.compute_average(rows),
      "discards" => discards,
      "cached" => false
    }

    # ❗ cache only non-runtime-failure results
    unless discards.any? { |d| d["reason"].include?("No such file or directory - node") }
      cache.set(cache_key, out)
    end

    out
  rescue Rules::EANError => e
    {
      "ean" => ean,
      "error" => e.message,
      "rows" => [],
      "average_rsv" => nil,
      "discards" => []
    }
  end

  { market: market, results: results }.to_json
end
