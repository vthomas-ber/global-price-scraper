FROM ruby:3.3.5-slim

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

ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app

# Node deps (Playwright JS)
COPY package.json package-lock.json /app/
RUN npm ci --omit=dev \
  && node -e "require('playwright'); console.log('playwright-ok')" \
  && ls -la /app/node_modules/playwright

# Ruby deps
COPY Gemfile Gemfile.lock /app/
RUN bundle install

# App
COPY . /app/

ENV RACK_ENV=production
EXPOSE 3000
CMD ["bundle", "exec", "ruby", "app.rb"]
