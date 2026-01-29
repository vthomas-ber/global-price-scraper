FROM ruby:3.3.5-slim

# --- System dependencies ---
# - pkg-config: needed for sqlite3 gem native build
# - nodejs + npm: required by Playwright driver (fixes "No such file or directory - node")
# - chromium + chromium-driver: browser runtime
RUN apt-get update -y && apt-get install -y \
  curl \
  ca-certificates \
  git \
  build-essential \
  pkg-config \
  libsqlite3-dev \
  chromium \
  chromium-driver \
  nodejs \
  npm \
  && rm -rf /var/lib/apt/lists/*

# --- Playwright config ---
# We use system Chromium and do NOT download Playwright browsers during build.
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app

# Install Ruby dependencies first (better caching)
COPY Gemfile Gemfile.lock ./
RUN bundle install

# Copy app code
COPY . .

ENV RACK_ENV=production

# Render provides PORT; Sinatra binds to 0.0.0.0 in app.rb
EXPOSE 3000

CMD ["bundle", "exec", "ruby", "app.rb"]
