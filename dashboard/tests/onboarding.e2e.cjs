// Full-stack local onboarding acceptance. No real bank/OAuth/mail/Slack/VPN actions.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { Pool } = require('pg');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.BASE_URL || 'http://127.0.0.1:3137';
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname) || !process.env.SCOUT_AUTH_TEST_DATABASE_URL) {
  throw new Error('Use an explicitly selected disposable local database and matching local server.');
}
const output = process.env.SCREENSHOT_DIR || path.resolve(__dirname, '../../artifacts/organization-auth/onboarding');
const run = randomUUID().slice(0, 8);
const slug = `setup-${run}`;
const password = 'local onboarding acceptance password';
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    const headers = { Origin: base, 'X-Scout-Organization': slug };
    const created = await context.request.post(`${base}/api/auth/register`, { headers: { Origin: base }, data: {
      name: 'Onboarding Test Studio', slug, ownerName: 'Setup Owner', email: `setup-${run}@example.test`, password,
    }});
    assert.equal(created.status(), 200, await created.text());
    assert.equal((await created.json()).redirect, `/${slug}#setup`, 'Signup enters setup only after creating the workspace');
    const page = await context.newPage();
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${base}/${slug}#setup`);
    await page.locator('#scout-onboarding').waitFor();
    let response = await context.request.get(`${base}/api/onboarding`, { headers });
    assert.equal(response.status(), 200, await response.text());
    let setup = await response.json();
    assert.equal(setup.version, 1);
    assert.deepEqual(setup.steps.map(step => step.id), ['bank', 'invoicing', 'ksef', 'email', 'slack', 'network']);
    assert.ok(['pending', 'in_progress'].includes(setup.status));
    assert.equal(setup.steps.some(step => ['connected', 'sandbox_connected'].includes(step.state)), false,
      'Signup never fabricates a successful connection');
    const channelUrl = `${base}/api/onboarding/slack/channels`;
    assert.equal((await context.request.put(channelUrl, { headers: { ...headers, Origin: 'https://other.example' }, data: { channels: [] } })).status(), 403);
    assert.equal((await context.request.put(channelUrl, { headers, data: { channels: ['general'] } })).status(), 400);
    // The Next boundary rebuilds an allowlisted body; unknown fields are dropped, not forwarded.
    const stripped = await context.request.put(channelUrl, { headers, data: { channels: [], token: 'not-accepted' } });
    assert.equal(stripped.status(), 200, await stripped.text());
    assert.equal((await stripped.text()).includes('not-accepted'), false);
    const uninstalledSlack = await context.request.put(channelUrl, { headers, data: { channels: ['C0123456789'] } });
    assert.equal(uninstalledSlack.status(), 200, await uninstalledSlack.text());
    setup = await uninstalledSlack.json();
    assert.notEqual(setup.steps.find(s => s.id === 'slack').state, 'connected');
    // Save a non-secret network intent. This sentinel must never enter shared context.
    const sentinel = `PRIVATE-NETWORK-ADMIN-${run}`;
    response = await context.request.patch(`${base}/api/onboarding`, { headers, data: { revision: setup.revision, action: 'network', network: {
      required: true, provider: 'WireGuard', protocol: 'WireGuard', systems: ['files'], administrator: sentinel, company_only: true,
    }}});
    assert.equal(response.status(), 200, await response.text());
    const staleRevision = setup.revision;
    setup = await response.json();
    assert.notEqual(setup.steps.find(step => step.id === 'network').state, 'connected', 'A questionnaire does not establish VPN connectivity');
    assert.equal((await context.request.patch(`${base}/api/onboarding`, { headers, data: { revision: staleRevision, action: 'skip' } })).status(), 409,
      'A stale update cannot silently overwrite a saved decision');
    const pool = new Pool({ connectionString: process.env.SCOUT_AUTH_TEST_DATABASE_URL });
    try {
      await pool.query("UPDATE scout_auth.memberships m SET role='member' FROM scout_auth.organizations o WHERE m.organization_id=o.id AND o.slug=$1", [slug]);
      assert.equal((await context.request.patch(`${base}/api/onboarding`, { headers, data: { revision: setup.revision, action: 'skip' } })).status(), 403,
        'A current member without owner privileges cannot change setup');
      assert.equal((await context.request.put(channelUrl, { headers, data: { channels: [] } })).status(), 403);
      assert.equal((await context.request.post(`${base}/api/onboarding/handoff`, { headers, data: {} })).status(), 403,
        'A current member without owner privileges cannot export setup context');
    } finally {
      await pool.query("UPDATE scout_auth.memberships m SET role='owner' FROM scout_auth.organizations o WHERE m.organization_id=o.id AND o.slug=$1", [slug]);
      await pool.end();
    }
    response = await context.request.post(`${base}/api/onboarding/handoff`, { headers, data: {} });
    assert.equal(response.status(), 200, await response.text());
    const handoff = await response.json();
    assert.equal(handoff.setupUrl, `${base}/${slug}#setup`);
    assert.equal(handoff.setupUrl.includes('token'), false);
    assert.equal(JSON.stringify(handoff).includes(sentinel), false, 'Shared handoff excludes network details');
    assert.equal(typeof handoff.markdown, 'string');
    assert.equal(typeof handoff.prompt, 'string');
    assert.ok(handoff.prompt.length > 20);
    assert.ok(handoff.prompt.length <= 5000);
    if (handoff.claudeUrl) {
      const shortcut = new URL(handoff.claudeUrl);
      assert.equal(shortcut.protocol, 'claude-cli:');
      assert.equal(shortcut.searchParams.has('repo'), false, 'Configuration does not require the Scout repository');
    }
    response = await context.request.post(`${base}/api/onboarding/connect/builtin`, { headers, data: {} });
    assert.equal(response.status(), 200, await response.text());
    setup = await response.json();
    assert.equal(setup.steps.find(step => step.id === 'invoicing').state, 'connected', 'Built-in invoicing is genuinely available in the private tenant');
    assert.equal((await context.request.patch(`${base}/api/onboarding`, { headers: { ...headers, Origin: 'https://other.example' }, data: { action: 'skip', revision: setup.revision } })).status(), 403);
    const anonymous = await browser.newContext();
    assert.equal((await anonymous.request.get(`${base}/api/onboarding`, { headers })).status(), 401);
    // A different valid workspace selector is not authorization to access its setup.
    const slugB = `setup-b-${run}`;
    const other = await anonymous.request.post(`${base}/api/auth/register`, { headers: { Origin: base }, data: {
      name: 'Other Setup Studio', slug: slugB, ownerName: 'Other Owner', email: `setup-b-${run}@example.test`, password,
    }});
    assert.equal(other.status(), 200, await other.text());
    assert.equal((await context.request.get(`${base}/api/onboarding`, { headers: { ...headers, 'X-Scout-Organization': slugB } })).status(), 401);
    // Fresh state is loaded before UI actions after the API update.
    await page.reload();
    await page.locator('#scout-onboarding').waitFor();
    // Exercise the real client -> JSON/CSRF guard -> backend disconnect path, not a mocked fetcher.
    await page.locator('#scout-onboarding-disconnect-invoicing').click();
    await page.locator('#scout-onboarding-step-invoicing[data-state="not_started"]').waitFor();
    await page.locator('#scout-onboarding-action-invoicing').click();
    await page.locator('#scout-onboarding-step-invoicing[data-state="connected"]').waitFor();
    for (const theme of ['light', 'dark']) {
      await page.evaluate(value => { localStorage.setItem('scout-theme', value); document.documentElement.classList.toggle('dark', value === 'dark'); }, theme);
      for (const width of [1440, 375, 320]) {
        await page.setViewportSize({ width, height: width > 400 ? 1000 : 812 });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${theme} ${width}: no horizontal overflow`);
        await page.screenshot({ path: path.join(output, `setup-${theme}-${width}.png`), fullPage: true });
      }
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator('#scout-onboarding-agent').click();
    await page.getByRole('dialog').waitFor();
    await page.getByRole('button', { name: 'Copy instructions', exact: true }).waitFor();
    await page.keyboard.press('Escape');
    await page.locator('#scout-onboarding-skip').click();
    await page.locator('#scout-chat-input').waitFor();
    response = await context.request.get(`${base}/api/onboarding`, { headers });
    assert.equal((await response.json()).status, 'dismissed');
    await page.goto(`${base}/${slug}`);
    await page.locator('#scout-chat-input').waitFor();
    assert.equal(await page.locator('#scout-onboarding').count(), 0, 'Dismissal survives a full reload');
    await page.locator('#scout-nav-connections').click();
    await page.locator('#scout-onboarding-reopen').click();
    await page.locator('#scout-onboarding').waitFor();
    assert.deepEqual(errors, []);
    console.log('PASS onboarding: post-signup, durable state, safe agent handoff, CSRF, two-org isolation, optional dismissal/reopen, mobile and light/dark. No live provider calls.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
