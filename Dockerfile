FROM mcr.microsoft.com/playwright:v1.45.0-jammy

# Install prerequisites
RUN apt-get update && apt-get install -y \
  curl gnupg build-essential libsqlite3-dev \
  && rm -rf /var/lib/apt/lists/*

# Install Ruby 3.4 via ruby-build (reliable and version-pinned)
RUN curl -fsSL https://github.com/rbenv/ruby-build/archive/refs/tags/v20241225.tar.gz \
  | tar xz -C /tmp \
  && /tmp/ruby-build-20241225/install.sh \
  && ruby-build 3.4.7 /usr/local \
  && rm -rf /tmp/ruby-build-20241225

WORKDIR /app

COPY Gemfile /app/
RUN gem install bundler && bundle install

COPY . /app/

ENV RACK_ENV=production
ENV PORT=3000

CMD ["bundle", "exec", "puma", "-p", "3000", "-e", "production", "config.ru"]
