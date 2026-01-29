# frozen_string_literal: true
require "net/http"
require "json"
require "uri"

class Serp
  GOLDEN_DOMAINS = {
    "DE" => %w[rewe.de edeka.de kaufland.de dm.de rossmann.de],
    "NL" => %w[ah.nl jumbo.com plus.nl dirk.nl vomar.nl],
    "BE" => %w[delhaize.be colruyt.be carrefour.be ah.be],
    "UK" => %w[tesco.com sainsburys.co.uk asda.com morrisons.com iceland.co.uk waitrose.com],
    "FR" => %w[carrefour.fr auchan.fr coursesu.com intermarche.com monoprix.fr franprix.fr],
    "DK" => %w[nemlig.com bilkatogo.dk rema1000.dk netto.dk],
    "IT" => %w[carrefour.it conad.it esselunga.it coop.it],
    "ES" => %w[carrefour.es mercadona.es dia.es alcampo.es],
    "PL" => %w[carrefour.pl auchan.pl biedronka.pl],
    "PT" => %w[continente.pt auchan.pt pingo-doce.pt],
    "SE" => %w[ica.se coop.se willys.se hemkop.se],
    "NO" => %w[oda.com meny.no spar.no]
  }.freeze

  # Phase 2 widening *within Germany only* (as per your requirement)
  EXTENDED_DOMAINS_DE = %w[
    aldi-nord.de
    aldi-sued.de
    lidl.de
    penny.de
    globus.de
    netto-online.de
    bringmeister.de
    flaschenpost.de
  ].freeze

  def initialize
    @api_key = ENV.fetch("SERPAPI_KEY")
  end

  def discover_urls(market:, ean:)
    domains = GOLDEN_DOMAINS.fetch(market) { [] }
    raise "No domain list configured for market=#{market}" if domains.empty?

    urls = []

    # Phase 1: Golden sites only
    urls.concat(search(ean: ean, domains: domains))

    # Phase 2: Automatically widen WITHIN Germany only
    if market == "DE" && urls.size < 5
      urls.concat(search(ean: ean, domains: EXTENDED_DOMAINS_DE))
    end

    # Dedup + stable order
    urls.uniq.take(15)
  end

  private

  def search(ean:, domains:)
    q = "#{ean} " + domains.map { |d| "site:#{d}" }.join(" OR ")

    uri = URI("https://serpapi.com/search.json")
    uri.query = URI.encode_www_form(
      engine: "google",
      q: q,
      api_key: @api_key,
      num: 10
    )

    res = Net::HTTP.get_response(uri)
    return [] unless res.is_a?(Net::HTTPSuccess)

    data = JSON.parse(res.body)
    organic = Array(data["organic_results"])
    organic.map { |r| r["link"] }.compact
  rescue StandardError
    []
  end
end
