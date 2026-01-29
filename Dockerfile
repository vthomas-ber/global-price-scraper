FROM ruby:3.3.5-slim

# System dependencies
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

# Playwright config: use system Chromium, no browser download
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app

# ---- Node deps (Playwright JS) ----
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

# ---- Ruby deps ----
COPY Gemfile Gemfile.lock ./
RUN bundle install

# ---- App code ----
COPY . .

ENV RACK_ENV=production
EXPOSE 3000

CMD ["bundle", "exec", "ruby", "app.rb"]
