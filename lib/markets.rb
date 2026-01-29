# frozen_string_literal: true
module Markets
  SUPPORTED = %w[FR UK NL BE DE DK IT ES SE NO PL PT].freeze

  GOLDEN_DOMAINS = {
    "FR" => %w[carrefour.fr auchan.fr coursesu.com intermarche.com monoprix.fr franprix.fr],
    "UK" => %w[tesco.com sainsburys.co.uk asda.com morrisons.com iceland.co.uk waitrose.com],
    "NL" => %w[ah.nl jumbo.com plus.nl dirk.nl vomar.nl],
    "BE" => %w[delhaize.be colruyt.be carrefour.be ah.be],
    "DE" => %w[rewe.de edeka.de kaufland.de dm.de rossmann.de],
    "DK" => %w[nemlig.com bilkatogo.dk rema1000.dk netto.dk],
    "IT" => %w[carrefour.it conad.it esselunga.it coop.it],
    "ES" => %w[carrefour.es mercadona.es dia.es alcampo.es],
    "SE" => %w[ica.se coop.se willys.se hemkop.se],
    "NO" => %w[oda.com meny.no spar.no],
    "PL" => %w[carrefour.pl auchan.pl biedronka.pl],
    "PT" => %w[continente.pt auchan.pt pingo-doce.pt]
  }.freeze
end
