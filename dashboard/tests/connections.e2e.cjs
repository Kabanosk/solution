// Requires a local authenticated organization server. No live connector writes are made.
const { chromium } = require('playwright-core');
const { authenticatedFixture } = require("./authenticated-browser.cjs");
let fixture;
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
let base = process.env.BASE_URL || 'http://127.0.0.1:3000';
const output = path.resolve(__dirname, '../../artifacts/connections');

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  try {
    fixture = await authenticatedFixture(browser);
    base = fixture.base;
    for (const theme of ['light', 'dark']) {
      const page = await fixture.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('console', message => { if (message.type() === 'error' && /hydrat/i.test(message.text())) errors.push('Hydration mismatch'); });
      await page.addInitScript(t => localStorage.setItem('scout-theme', t), theme);
      await page.goto(base + '/#connections');
      await page.getByRole('heading', { name: 'Connections', exact: true }).waitFor();
      assert.equal(await page.locator('[data-connection-row]').count(), 6, 'Integrations form a consistent six-row list');
      assert.equal(await page.locator('#scout-connections [data-slot="card"]').count(), 0, 'Connections are not a mismatched card grid');
      await page.getByText('5 of 6 connected', { exact: true }).waitFor();
      assert.equal(await page.locator('#scout-connect-google').isEnabled(), true, 'Owners can connect a missing mailbox from Connections');
      await page.locator('#scout-connect-google').click();
      await page.locator('#scout-onboarding').waitFor();
      await page.getByRole('button', { name: 'Continue with Google', exact: true }).waitFor();
      await page.goto(base + '/#connections');
      await page.getByRole('heading', { name: 'Connections', exact: true }).waitFor();
      assert.equal(await page.locator('#ksef-invoices').count(), 0, 'Detailed sync output is hidden until requested');
      for (const row of await page.locator('[data-connection-row]').all()) {
        assert.equal(await row.locator('[data-connection-icon] svg').evaluate(e => getComputedStyle(e).width), '16px');
      }
      const ksefDetails = page.getByRole('button', { name: 'Show KSeF details', exact: true });
      await ksefDetails.focus();
      await page.keyboard.press('Enter');
      await page.locator('#ksef-invoices').waitFor();
      await page.locator('#ksef-invoices').getByText('VISUAL-1', { exact: true }).waitFor();
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
      assert.equal(await page.locator('#ksef-connect').isEnabled(), true, 'Owners can revisit KSeF setup without reopening old global mutations');
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
      assert.equal(await helpLink.getAttribute('href'), '#connections', 'Dashboard help preserves the organization path');
      await helpLink.click();
      await page.getByRole('heading', { name: 'Connections', exact: true }).waitFor();
      assert.equal(new URL(page.url()).pathname.replace(/\/$/, ''), `/${fixture.slug}`);
      await page.goto(base + '/#overview');
      await page.locator('#scout-empty-overview').waitFor({ state: 'visible' });
      await page.getByRole('heading', { name: 'Your workspace is ready', exact: true }).waitFor();
      assert.deepEqual(errors, []);
      console.log(`PASS ${theme}: integration list, keyboard details, scopes, Google setup/callback, disabled connection mutations, responsive layouts, default chat and explicit Overview`);
      await page.close();
    }
    // Existing workspaces with no setup record and no integration rows must still be able to connect later.
    const { Pool } = require('pg');
    const pool = new Pool({ connectionString: process.env.SCOUT_AUTH_TEST_DATABASE_URL });
    try {
      const org = (await pool.query('SELECT id, schema_name FROM scout_auth.organizations WHERE slug=$1', [fixture.slug])).rows[0];
      assert.match(org.schema_name, /^tenant_[a-f0-9]{32}$/);
      await pool.query(`DELETE FROM "${org.schema_name}".connections`);
      await pool.query('DELETE FROM scout_auth.workspace_setup WHERE organization_id=$1', [org.id]);
    } finally { await pool.end(); }
    const emptyPage = await fixture.newPage({ viewport: { width: 375, height: 812 } });
    for (const id of ['scout-connect-bank', 'scout-connect-invoicing', 'ksef-connect', 'scout-connect-google', 'scout-connect-slack', 'scout-connect-laptops']) {
      await emptyPage.goto(base + '/#connections');
      await emptyPage.getByRole('heading', { name: 'Connections', exact: true }).waitFor();
      assert.equal(await emptyPage.locator(`#${id}`).isEnabled(), true, `${id}: missing integrations can be added`);
      await emptyPage.locator(`#${id}`).click();
      await emptyPage.locator('#scout-onboarding').waitFor();
      assert.match(emptyPage.url(), /#setup\?step=(bank|invoicing|ksef|email|slack|network)$/);
      await emptyPage.locator('[data-onboarding-step]').first().waitFor();
      assert.equal(await emptyPage.locator('[data-onboarding-step]').count(), 1);
      assert.equal(await emptyPage.locator('#scout-sidebar, #scout-mobile-navigation').count(), 0);
    }
    await emptyPage.close();
    console.log('PASS existing empty workspace: all Connections setup actions work without a prior onboarding record');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
