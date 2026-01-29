# frozen_string_literal: true

module Rules
  class EANError < StandardError; end

  # EAN-13 checksum validation
  def self.valid_ean13?(ean)
    return false unless ean.match?(/\A\d{13}\z/)

    digits = ean.chars.map(&:to_i)
    sum = 0

    # indices 0..11 are data, 12 is check digit
    (0..11).each do |i|
      sum += (i.even? ? digits[i] : digits[i] * 3)
    end

    check = (10 - (sum % 10)) % 10
    check == digits[12]
  end

  def self.validate_ean!(ean)
    raise EANError, "EAN must be digits only" unless ean.match?(/\A\d+\z/)

    if ean.length == 13
      raise EANError, "EAN-13 checksum failed (possible typo)" unless valid_ean13?(ean)
    elsif ean.length == 8
      # Allow EAN-8 for now (optional to implement checksum later)
    else
      raise EANError, "EAN must be 13 digits (EAN-13) or 8 digits (EAN-8)"
    end
  end

  # Guardrails against bulk/multibuy/promo mechanics contaminating RSV
  FORBIDDEN_CONTEXT_PATTERNS = [
    /2\s*(für|for)\s*/i,
    /3\s*(für|for)\s*/i,
    /mix\s*&\s*match/i,
    /mehrkauf/i,
    /bundle/i,
    /\bab\b\s*\d/i,
    /\bfrom\b\s*\d/i
  ].freeze

  def self.forbidden_context?(text)
    t = text.to_s
    FORBIDDEN_CONTEXT_PATTERNS.any? { |re| re.match?(t) }
  end

  def self.compute_average(rows)
    vals = rows
      .select { |r| r["price_type_flag"] == "Regular" }
      .select { |r| r["comparable"] == true }
      .map { |r| r["rsv"] }
      .compact
      .map(&:to_f)

    return nil if vals.empty?
    (vals.sum / vals.size).round(2)
  end
end
