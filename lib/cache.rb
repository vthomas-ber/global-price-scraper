# frozen_string_literal: true
require "sequel"
require "json"

class Cache
  def initialize
    @db = Sequel.sqlite(ENV.fetch("CACHE_DB", "cache.sqlite3"))

    @db.create_table? :cache do
      String :key, primary_key: true
      String :value, text: true
      Integer :created_at
    end
  end

  def get(key)
    row = @db[:cache].where(key: key).first
    return nil unless row
    JSON.parse(row[:value])
  rescue StandardError
    nil
  end

  def set(key, value)
    payload = JSON.dump(value)
    now = Time.now.to_i

    @db[:cache]
      .insert_conflict(target: :key, update: { value: payload, created_at: now })
      .insert(key: key, value: payload, created_at: now)
  rescue StandardError
    # best-effort cache
  end
end
