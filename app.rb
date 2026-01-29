# frozen_string_literal: true
require "sinatra"
require "dotenv/load"
require "json"

require_relative "./lib/markets"
require_relative "./lib/rules"
require_relative "./lib/price_pipeline"

set :bind, "0.0.0.0"
set :port, ENV.fetch("PORT", "3000").to_i

get "/" do
  @markets = Markets::SUPPORTED
  @market = "DE"
  @eans_text = ""
  @results = nil
  erb :index
end

post "/run" do
  @markets = Markets::SUPPORTED
  @market = (params["market"] || "").strip.upcase
  eans_text = (params["eans"] || "")
  @eans_text = eans_text

  eans = eans_text.lines.map(&:strip).reject(&:empty?)
  begin
    Rules.validate_market!(@market)
    Rules.validate_ean_list!(eans)
  rescue => e
    @results = [{ ean: "-", error: e.message, rows: [], master: {} }]
    return erb :index
  end

  pipeline = PricePipeline.new
  @results = eans.map { |ean| pipeline.run(market: @market, ean: ean) }
  erb :index
end

# API endpoint (optional)
post "/scrape" do
  content_type :json
  body = request.body.read.to_s
  payload = body.empty? ? {} : JSON.parse(body)

  market = (payload["market"] || "").strip.upcase
  eans = Array(payload["eans"]).map(&:to_s).map(&:strip).reject(&:empty?)

  Rules.validate_market!(market)
  Rules.validate_ean_list!(eans)

  pipeline = PricePipeline.new
  results = eans.map { |ean| pipeline.run(market: market, ean: ean) }

  { market: market, results: results }.to_json
end
