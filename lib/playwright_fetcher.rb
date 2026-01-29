# frozen_string_literal: true
require "open3"
require "json"

class PlaywrightFetcher
  def initialize
    @timeout_ms = (ENV["PW_TIMEOUT_MS"] || "20000").to_i
    @locale = ENV["PW_LOCALE"] || "de-DE"
  end

  # Returns Hash: { "url", "finalUrl", "html", "text" }
  def fetch(url)
    script = <<~'JS'
      const { chromium } = require('playwright');

      (async () => {
        const url = process.argv[2];
        const timeout = parseInt(process.env.PW_TIMEOUT_MS || "20000", 10);
        const locale = process.env.PW_LOCALE || "de-DE";

        const browser = await chromium.launch({ headless: true });
        const context = await browser.newContext({ locale });

        const page = await context.newPage();
        page.setDefaultTimeout(timeout);

        // Speed: block images/fonts
        await page.route('**/*', (route) => {
          const type = route.request().resourceType();
          if (type === 'image' || type === 'font') return route.abort();
          return route.continue();
        });

        await page.goto(url, { waitUntil: 'domcontentloaded' });
        await page.waitForTimeout(1500); // allow JS price widgets to paint

        const finalUrl = page.url();
        const html = await page.content();
        const text = await page.evaluate(() => document.body.innerText);

        await browser.close();

        process.stdout.write(JSON.stringify({ url, finalUrl, html, text }));
      })().catch(err => {
        process.stderr.write(String(err));
        process.exit(1);
      });
    JS

    cmd = ["node", "-e", script, url]
    env = {
      "PW_TIMEOUT_MS" => @timeout_ms.to_s,
      "PW_LOCALE" => @locale
    }

    out, err, status = Open3.capture3(env, *cmd)
    raise "Playwright fetch failed: #{err}" unless status.success?

    JSON.parse(out)
  end
end
