// Requires a local DASHBOARD_PREVIEW=1 server. No live connector writes are made.
const { chromium } = require('playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.BASE_URL || 'http://127.0.0.1:3000';
const output = path.resolve(__dirname, '../../artifacts/connections');

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  try {
    for (const theme of ['light', 'dark']) {
      const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('console', message => { if (message.type() === 'error' && /hydrat/i.test(message.text())) errors.push('Hydration mismatch'); });
      await page.addInitScript(t => localStorage.setItem('scout-theme', t), theme);
      await page.goto(base + '/#connections');
      await page.getByRole('heading', { name: 'Connections', exact: true }).waitFor();
      assert.equal(await page.locator('[data-connection-row]').count(), 6, 'Integrations form a consistent six-row list');
      assert.equal(await page.locator('#scout-connections [data-slot="card"]').count(), 0, 'Connections are not a mismatched card grid');
      await page.getByText('5 of 6 connected', { exact: true }).waitFor();
      assert.equal(await page.locator('#scout-connect-google').getAttribute('href'), '/api/google');
      assert.equal(await page.locator('#ksef-invoices').count(), 0, 'Detailed sync output is hidden until requested');
      for (const row of await page.locator('[data-connection-row]').all()) {
        assert.equal(await row.locator('[data-connection-icon] svg').evaluate(e => getComputedStyle(e).width), '16px');
      }
      const ksefDetails = page.getByRole('button', { name: 'Show KSeF details', exact: true });
      await ksefDetails.focus();
      await page.keyboard.press('Enter');
      await page.locator('#ksef-invoices').waitFor();
      assert.equal(await page.getByRole('button', { name: 'Hide KSeF details', exact: true }).getAttribute('aria-expanded'), 'true');
      await page.getByRole('button', { name: 'Hide KSeF details', exact: true }).click();
      await page.getByRole('button', { name: 'Show Slack details', exact: true }).click();
      await page.getByText('channels:join', { exact: false }).waitFor();
      await page.getByRole('button', { name: 'Hide Slack details', exact: true }).click();
      await page.getByRole('button', { name: 'Show Email details', exact: true }).click();
      await page.getByText('Google Workspace (admin)', { exact: true }).click();
      await page.getByText('Enable the Gmail API in the same project.', { exact: true }).waitFor();
      await page.getByRole('button', { name: 'Hide Email details', exact: true }).click();
      await page.mouse.move(0, 0);
      await page.screenshot({ path: path.join(output, `connections-${theme}.png`), fullPage: true });
      await page.route('**/api/ksef', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'The integration is temporarily unavailable.' }) }));
      await page.locator('#ksef-connect').click();
      await page.getByRole('alert').filter({ hasText: 'The integration is temporarily unavailable.' }).waitFor();
      assert.equal(await page.locator('#ksef-connect').isEnabled(), true, 'A failed sync remains retryable');
      assert.equal(await page.getByRole('button', { name: 'Hide KSeF details', exact: true }).getAttribute('aria-expanded'), 'true');
      for (const viewport of [{ width: 320, height: 640 }, { width: 375, height: 812 }, { width: 812, height: 375 }]) {
        await page.setViewportSize(viewport);
        await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth, undefined, { timeout: 5000 });
        await page.screenshot({ path: path.join(output, `connections-${theme}-${viewport.width}.png`), fullPage: true });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      }
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.goto(base + '/?google=failed#connections');
      await page.getByRole('alert').filter({ hasText: 'Google connection failed. Try again.' }).waitFor();
      assert.equal(new URL(page.url()).searchParams.has('google'), false, 'OAuth callback is consumed once');
      await page.goto(base + '/?google=connected#connections');
      await page.getByRole('status').filter({ hasText: 'Google connected. Scout can now read the authorized mail label.' }).waitFor();
      assert.equal(await page.locator('#scout-connections-notice').evaluate(element => element.classList.contains('text-danger')), false, 'Successful OAuth is not shown as an error');
      await page.goto(base + '/');
      await page.locator('#scout-chat-input').waitFor({ state: 'visible' });
      await page.getByRole('heading', { name: 'Ask Scout', exact: true }).waitFor();
      assert.equal(await page.locator('#scout-nav-chat').getAttribute('aria-current'), 'page', 'Root opens Scout chat by default');
      await page.route('**/api/chat', route => route.fulfill({ status: 200, contentType: 'application/x-ndjson', body: [
        { type: 'step', text: 'Looked at dashboard help(topic=connections)' },
        { type: 'delta', text: 'Open [Connections](/#connections) to review integration status and expand its details.' },
        { type: 'done', truncated: false },
      ].map(event => JSON.stringify(event)).join('\n') + '\n' }));
      await page.getByRole('button', { name: 'How do I use this dashboard?', exact: true }).click();
      await page.locator('#scout-chat-input').press('Enter');
      const helpLink = page.locator('#scout-chat-messages').getByRole('link', { name: 'Connections', exact: true });
      await helpLink.waitFor();
      assert.equal(await helpLink.getAttribute('target'), null, 'Dashboard help navigates in this tab');
      await helpLink.click();
      await page.getByRole('heading', { name: 'Connections', exact: true }).waitFor();
      await page.goto(base + '/#overview');
      await page.locator('#scout-overview').waitFor({ state: 'visible' });
      assert.deepEqual(errors, []);
      console.log(`PASS ${theme}: integration list, keyboard details, scopes, Google setup/callback, sync error/retry, responsive layouts, default chat and explicit Overview`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
