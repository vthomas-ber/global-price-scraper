FROM ruby:3.3.5-slim

# System dependencies
RUN apt-get update -y && apt-get install -y \
  curl \
  ca-certificates \
  git \
  build-essential \
  libsqlite3-dev \
  chromium \
  chromium-driver \
  && rm -rf /var/lib/apt/lists/*

# Playwright needs this
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app

# Ruby deps
COPY Gemfile Gemfile.lock ./
RUN bundle install --without development test

# App
COPY . .

# Render sets PORT automatically
ENV RACK_ENV=production

EXPOSE 3000

CMD ["bundle", "exec", "ruby", "app.rb"]
