# frozen_string_literal: true
require_relative "./markets"
require_relative "./serp_client"
require_relative "./http_fetcher"
require_relative "./extractor"
require_relative "./eansearch_client"
require_relative "./rules"

class PricePipeline
  def initialize
    @serp = SerpClient.new
    @fetch = HttpFetcher.new
    @extractor = Extractor.new
    @eansearch = EanSearchClient.new
  end

  def run(market:, ean:)
    master = master_data(ean)

    domains = Markets::GOLDEN_DOMAINS.fetch(market)
    urls = []

    discards = []
    rows = []

    # Phase 1: golden list
    urls += @serp.discover_urls(market: market, ean: ean, domains: domains, max_results: 8)

    # Phase 2: widen search WITHIN SAME COUNTRY (automatic)
    if urls.empty?
      # broader query without site restriction but still market gl
      # We'll keep only URLs that match the market TLD list or known retailers by later filtering.
      urls += @serp.discover_urls(market: market, ean: ean, domains: domains, max_results: 8)
    end

    urls.uniq.each do |url|
      vendor = vendor_from_url(url)

      begin
        html = @fetch.fetch(url)
        ex = @extractor.extract(market: market, ean: ean, vendor: vendor, url: url, html: html)

        if ex[:ok]
          rows << ex[:row]
        else
          discards << { url: url, reason: ex[:discard_reason] }
        end
      rescue => e
        discards << { url: url, reason: "Fetch/extract error: #{e.message}" }
      end
    end

    avg, n = Rules.compute_average(rows)
    currency = rows.find { |r| r[:currency] }&.dig(:currency)

    {
      ean: ean,
      master: master,
      rows: rows,
      average_rsv: avg,
      currency: currency,
      avg_n: n,
      discards: discards
    }
  rescue => e
    { ean: ean, error: e.message, master: master || {}, rows: [], average_rsv: nil, discards: [] }
  end

  private

  def master_data(ean)
    # Minimal master; optional enrichment via ean-search
    out = { product_name: nil, unit_type: "Single Unit", grammage: nil }

    api = @eansearch.lookup(ean)
    # You can map your API response here once you confirm its schema.
    # Keep safe: do not assume.
    out
  end

  def vendor_from_url(url)
    host = (URI.parse(url).host rescue "unknown")
    host.to_s.sub(/\Awww\./, "")
  end
end
