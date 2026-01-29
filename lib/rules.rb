# frozen_string_literal: true
module Rules
  class RuleError < StandardError; end

  def self.validate_market!(market)
    raise RuleError, "market is required" if market.to_s.empty?
    raise RuleError, "Unsupported market #{market}" unless Markets::SUPPORTED.include?(market)
  end

  def self.validate_ean_list!(eans)
    raise RuleError, "Provide 1–10 EANs" if eans.empty? || eans.size > 10
    eans.each { |e| validate_ean!(e) }
  end

  def self.validate_ean!(ean)
    s = ean.to_s.strip
    raise RuleError, "EAN must be digits only: #{ean}" unless s.match?(/\A\d+\z/)
    raise RuleError, "EAN must be 8, 12, 13, or 14 digits: #{ean}" unless [8,12,13,14].include?(s.length)

    # checksum validation for GTIN-8/12/13/14 (common)
    return true if checksum_valid?(s)

    # Still allow if you want strict? You said strict. So fail:
    raise RuleError, "EAN checksum invalid (possible typo): #{ean}"
  end

  def self.checksum_valid?(digits)
    ds = digits.chars.map(&:to_i)
    check = ds.pop
    # weighting depends on length but standard GTIN uses alternating 3/1 from right
    sum = 0
    ds.reverse.each_with_index do |d, idx|
      sum += d * (idx.even? ? 3 : 1)
    end
    calc = (10 - (sum % 10)) % 10
    calc == check
  end

  def self.compute_average(rows)
    comparable = rows.select { |r| r[:flag] == "Comparable" && r[:rsv].is_a?(Numeric) }
    return [nil, 0] if comparable.empty?
    avg = comparable.map { |r| r[:rsv] }.sum / comparable.size.to_f
    [avg.round(2), comparable.size]
  end
end
