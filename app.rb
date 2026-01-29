# frozen_string_literal: true
require "sinatra"
require "json"
require "dotenv/load"

require_relative "./lib/serp"
require_relative "./lib/playwright_fetcher"
require_relative "./lib/extractor"
require_relative "./lib/rules"
require_relative "./lib/cache"

set :bind, "0.0.0.0"
set :port, ENV.fetch("PORT", "3000").to_i

# ----------------------------------------------------
# Codespaces / Dev: Allow forwarded hosts
# ----------------------------------------------------
# Sinatra 4 enables Rack::Protection::HostAuthorization. In Codespaces,
# the forwarded host is something like: <name>-3000.app.github.dev
#
# rack-protection expects OPTIONS AS A HASH (not false), so we configure it.
#
configure do
  if ENV["CODESPACES"] == "true" || settings.environment == :development
    set :protection, {
      host_authorization: {
        permitted_hosts: [
          /.*\.app\.github\.dev\z/,
          /.*\.githubpreview\.dev\z/,
          /.*\.github\.dev\z/,
          "localhost",
          "127.0.0.1"
        ]
      }
    }
  end
end
# ----------------------------------------------------

helpers do
  def json_body
    request.body.rewind
    raw = request.body.read
    raw.empty? ? {} : JSON.parse(raw)
  rescue JSON::ParserError
    halt 400, { error: "Invalid JSON" }.to_json
  end
end

before do
  puts "HOST_SEEN=#{request.host} HTTP_HOST=#{request.env['HTTP_HOST']} X_FORWARDED_HOST=#{request.env['HTTP_X_FORWARDED_HOST']}"
end

get "/__debug_host" do
  content_type :json
  {
    rack_env: ENV["RACK_ENV"],
    sinatra_env: settings.environment.to_s,
    codespaces: ENV["CODESPACES"],
    host: request.host,
    http_host: request.env["HTTP_HOST"],
    forwarded_host: request.env["HTTP_X_FORWARDED_HOST"],
    forwarded_proto: request.env["HTTP_X_FORWARDED_PROTO"]
  }.to_json
end

get "/" do
  erb :index
end

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

    cache.set(cache_key, out)
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
