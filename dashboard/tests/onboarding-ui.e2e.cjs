// Browser acceptance for the onboarding UI. Needs a local server + isolated DB (BASE_URL, SCOUT_AUTH_TEST_DATABASE_URL).
// IMPORTANT: every /api/onboarding** response here is a controlled in-browser MOCK of the server contract. Signup and
// the dashboard session are real; no provider (bunq, Google, Slack, KSeF) is ever contacted. Real-DB acceptance is separate.
const { chromium } = require('playwright');
const { Pool } = require('pg');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { authenticatedFixture } = require('./authenticated-browser.cjs');
const output = process.env.SCREENSHOT_DIR || path.resolve(__dirname, '../../artifacts/onboarding-ui');
const IDS = ['bank', 'invoicing', 'ksef', 'email', 'slack', 'network'];
const step = (id, state = 'not_started', extra = {}) => ({ id, state, provider: null, message: null, display: {}, ...extra });
const fresh = (status = 'pending') => ({ version: 1, status, revision: 1, steps: IDS.map(id => step(id)), network: null });

/** In-browser mock of the onboarding contract. `opts` flips failure modes per test. */
async function mockServer(page, initial, opts = {}) {
  const state = { setup: initial, calls: [], conflictOnce: false, ...opts };
  const reply = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  const set = (id, patch) => { state.setup.steps = state.setup.steps.map(entry => entry.id === id ? { ...entry, ...patch } : entry); state.setup.revision += 1; };
  await page.route('**/api/onboarding/**', async route => {
    const request = route.request(); const url = new URL(request.url()); const method = request.method();
    const body = request.postData() ? JSON.parse(request.postData()) : null;
    state.calls.push({ method, path: url.pathname, body });
    if (url.pathname === '/api/onboarding/handoff') return reply(route, { setupUrl: `${url.origin}/x#setup`, markdown: '# Scout setup\nSign in yourself.', manifest: { version: 'scout.setup/v1', steps: [] }, prompt: 'Help me finish Scout setup. I sign in myself.', claudeUrl: 'claude-cli://open?q=hello' });
    if (url.pathname === '/api/onboarding/slack/channels' && method === 'PUT') {
      set('slack', { state: body.channels.length ? 'connected' : 'needs_configuration', provider: 'slack', display: { authorized: true, workspace: 'Test Slack', channels: String(body.channels.length), channel_ids: body.channels.join(', ') } });
      return reply(route, state.setup);
    }
    const provider = url.pathname.split('/').pop();
    const target = { bunq: 'bank', builtin: 'invoicing', ksef: 'ksef', google: 'email', slack: 'slack' }[provider];
    if (method === 'DELETE') { set(target, { state: 'not_started', display: {}, message: null }); return reply(route, state.setup); }
    if (provider === 'google' || provider === 'slack') {
      if (state.failProvider === provider) return reply(route, { error: `${provider} is unavailable` }, 502);
      if (state.missingCredentials === provider) { set(target, { state: 'needs_configuration', message: 'Google mailbox credentials are not configured on this server.' }); return reply(route, state.setup); }
      return reply(route, { url: `${url.origin}/__mock-oauth/${provider}` });
    }
    if (state.failProvider === provider) return reply(route, { error: 'bunq sandbox is unreachable' }, 502);
    if (provider === 'bunq') set('bank', { state: 'sandbox_connected', provider: 'bunq', display: { account: 'Sandbox company', mode: 'sandbox' } });
    if (provider === 'builtin') set('invoicing', { state: 'connected', provider: 'builtin', display: { documents: '0' } });
    if (provider === 'ksef') set('ksef', { state: 'needs_configuration', provider: 'ksef', message: 'Test-environment authentication is not configured on this server.' });
    return reply(route, state.setup);
  });
  await page.route(/\/api\/onboarding$/, async route => {
    const request = route.request(); const method = request.method();
    const body = request.postData() ? JSON.parse(request.postData()) : null;
    state.calls.push({ method, path: '/api/onboarding', body });
    if (method === 'GET') return reply(route, state.setup);
    if (state.conflictOnce) { state.conflictOnce = false; state.setup = { ...state.setup, revision: state.setup.revision + 5 }; return reply(route, { error: 'Revision conflict' }, 409); }
    if (state.failPatch) return reply(route, { error: 'Setup could not be saved' }, 503);
    if (body.revision !== state.setup.revision) return reply(route, { error: 'Revision conflict' }, 409);
    if (body.action === 'skip') { state.setup.status = 'dismissed'; state.setup.revision += 1; }
    else if (body.action === 'complete') { state.setup.status = 'completed'; state.setup.revision += 1; }
    else if (body.action === 'step') set(body.step, { state: body.state });
    else if (body.action === 'network') { state.setup.network = body.network; set('network', { state: body.network.required ? 'needs_configuration' : 'skipped', message: body.network.required ? 'An administrator must set up private access.' : null }); }
    return reply(route, state.setup);
  });
  return state;
}

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const pool = new Pool({ connectionString: process.env.SCOUT_AUTH_TEST_DATABASE_URL });
  try {
    // Dev servers on busy machines are slow: generous, explicit timeouts instead of flaky defaults.
    const patient = fixture => { const original = fixture.newPage; fixture.newPage = async options => { const page = await original(options); page.setDefaultTimeout(60000); page.setDefaultNavigationTimeout(120000); return page; }; return fixture; };
    const fixture = patient(await authenticatedFixture(browser));
    const base = fixture.base;
    const open = async (initial, hash = '', opts, viewport = { width: 1440, height: 1000 }, theme = 'light') => {
      const page = await fixture.newPage({ viewport, reducedMotion: 'reduce', permissions: ['clipboard-read', 'clipboard-write'] });
      page.errors = []; page.on('pageerror', error => page.errors.push(error.message));
      await page.addInitScript(t => localStorage.setItem('scout-theme', t), theme);
      const server = await mockServer(page, initial, opts);
      await page.goto(base + (hash ? '/' + hash : '/'), { waitUntil: 'commit' });
      if (hash.includes('#setup') || (!hash && ['pending', 'in_progress'].includes(initial.status))) await page.locator('[data-onboarding-step]').first().waitFor();
      return { page, server };
    };
    const stepBox = (page, id) => page.locator(`#scout-onboarding-step-${id}`);
    const status = (page, id) => stepBox(page, id).locator('[data-onboarding-status]').innerText();
    const shown = page => page.locator('[data-onboarding-step]').evaluateAll(nodes => nodes.map(node => node.dataset.onboardingStep));
    // Sequential wizard: only the current step is rendered. Moves with the real Next button (explicit skips in the mock server).
    const advance = async (page, to) => { await page.locator('#scout-onboarding-next').click(); await page.locator(`#scout-onboarding-step-${to}`).waitFor(); };
    const walkTo = async (page, id) => { for (let guard = 0; guard < 6; guard++) { const [now] = await shown(page); if (now === id) return; await advance(page, IDS[IDS.indexOf(now) + 1]); } assert.deepEqual(await shown(page), [id]); };
    const settledFrom = (setup, ids, state = 'skipped') => { setup.steps = setup.steps.map(entry => ids.includes(entry.id) ? step(entry.id, state) : entry); return setup; };

    // 1. A pending workspace opens setup on an ordinary load, showing ONE step at a time: Step N of 6, contract order and exact CTAs.
    for (const theme of ['light', 'dark']) {
      const { page } = await open(fresh(), '', undefined, undefined, theme);
      await page.locator('#scout-onboarding').waitFor();
      assert.match(page.url(), /#setup$/);
      await page.getByRole('heading', { name: 'Your workspace is ready', exact: true }).waitFor();
      assert.deepEqual(await shown(page), ['bank'], 'only the first step is rendered');
      assert.equal(await page.locator('#scout-onboarding-progress-label').innerText(), 'Step 1 of 6');
      const bar = page.getByRole('progressbar', { name: 'Setup progress' });
      assert.deepEqual(await bar.evaluate(node => [node.getAttribute('aria-valuenow'), node.getAttribute('aria-valuemax'), node.getAttribute('aria-valuetext')]), ['1', '6', 'Step 1 of 6: Bank']);
      assert.equal(await page.locator('#scout-onboarding-back').isDisabled(), true, 'no Back on the first step');
      assert.equal(await page.getByRole('button', { name: 'Next', exact: true }).count(), 1);
      await page.getByRole('button', { name: 'Continue with your agent', exact: true }).waitFor();
      await page.getByText('You can finish setup from Connections.', { exact: false }).waitFor();
      const ctas = [];
      for (const [index, id] of IDS.entries()) {
        assert.deepEqual(await shown(page), [id]);
        assert.equal(await page.locator('#scout-onboarding-progress-label').innerText(), `Step ${index + 1} of 6`);
        ctas.push((await page.locator(`#scout-onboarding-action-${id}`).innerText()).trim());
        if (index < IDS.length - 1) await advance(page, IDS[index + 1]);
      }
      assert.deepEqual(ctas, ['Connect sandbox bank', 'Use built-in invoicing', 'Set up test environment', 'Continue with Google', 'Connect workspace', 'Answer a few questions']);
      await page.getByRole('button', { name: 'Finish setup', exact: true }).waitFor();
      await page.locator('#scout-onboarding-back').click(); await page.locator('#scout-onboarding-step-slack').waitFor();
      assert.equal(await page.locator('#scout-onboarding-progress-label').innerText(), 'Step 5 of 6', 'Back returns without saving');
      await page.locator('#scout-onboarding-next').click(); await page.locator('#scout-onboarding-step-network').waitFor();
      // Semantic tokens only: no raw palette classes inside the setup UI.
      const raw = await page.locator('#scout-onboarding, #scout-onboarding *').evaluateAll(nodes => nodes.filter(node => /(^|\s)(bg|text|border|ring|outline|accent)-(red|green|blue|amber|emerald|teal|slate|gray|zinc|neutral|stone|yellow|orange|sky|indigo)-\d{2,3}/.test(node.getAttribute('class') || '') || /#[0-9a-f]{3,8}\b/i.test(node.getAttribute('style') || '')).length);
      assert.equal(raw, 0, 'no raw colours');
      const ids = await page.evaluate(() => { const all = [...document.querySelectorAll('[id]')].map(node => node.id); return all.length - new Set(all).size; });
      assert.equal(ids, 0, 'unique ids');
      for (const viewport of [{ width: 1440, height: 1000 }, { width: 375, height: 812 }, { width: 320, height: 640 }]) {
        await page.setViewportSize(viewport);
        await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth, undefined, { timeout: 5000 });
        await page.screenshot({ path: path.join(output, `setup-${theme}-${viewport.width}.png`), fullPage: true });
      }
      assert.deepEqual(page.errors, []);
      await page.close();
    }

    // 2. Legacy (not_started) and dismissed/completed organizations stay on chat; explicit hashes are never overridden.
    for (const status of ['not_started', 'dismissed', 'completed']) {
      const { page } = await open(fresh(status));
      await page.locator('#scout-chat-input').waitFor({ state: 'visible' });
      await page.waitForTimeout(400);
      assert.equal(await page.locator('#scout-onboarding').count(), 0, status);
      await page.close();
    }
    { const { page, server } = await open(fresh(), '#tasks');
      await page.locator('#scout-nav-tasks').waitFor(); await page.waitForTimeout(400);
      assert.equal(await page.locator('#scout-onboarding').count(), 0, 'deep link preserved');
      assert.equal(server.calls.length, 0, 'no setup lookup when a hash is explicit');
      await page.close(); }
    { const { page } = await open(fresh(), '#chat');
      await page.locator('#scout-chat-input').waitFor({ state: 'visible' }); await page.waitForTimeout(400);
      assert.equal(await page.locator('#scout-onboarding').count(), 0, '#chat preserved'); await page.close(); }
    { const { page } = await open(fresh('pending'), '#setup'); await page.locator('#scout-onboarding').waitFor();
      await page.reload({ waitUntil: 'commit' }); await page.locator('#scout-onboarding').waitFor(); await page.close(); }

    // 3. Load error shows a retry, never an empty success.
    { const page = await fixture.newPage({ viewport: { width: 1440, height: 1000 } });
      let fail = true;
      await page.route(/\/api\/onboarding$/, route => fail ? route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"Setup is down"}' }) : route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fresh()) }));
      await page.goto(base + '/#setup', { waitUntil: 'commit' });
      await page.getByRole('alert').filter({ hasText: 'Setup is down' }).waitFor();
      assert.equal(await page.locator('[data-onboarding-step]').count(), 0);
      fail = false; await page.locator('#scout-onboarding-retry').click();
      await page.locator('[data-onboarding-step]').first().waitFor(); await page.close(); }

    // 4. Connect states are labelled truthfully; the verified status is shown BEFORE Next (no auto-advance); failures never advance.
    { const { page, server } = await open(fresh(), '#setup', { failProvider: 'bunq' });
      await page.locator('#scout-onboarding-action-bank').click();
      await page.getByRole('alert').filter({ hasText: 'bunq sandbox is unreachable' }).waitFor();
      assert.equal(await status(page, 'bank'), 'Not started');
      assert.deepEqual(await shown(page), ['bank'], 'a failed connect does not advance');
      server.failProvider = null;
      await page.locator('#scout-onboarding-action-bank').click();
      await page.waitForFunction(() => document.querySelector('#scout-onboarding-status-bank')?.textContent.includes('Sandbox connected'));
      assert.equal(await status(page, 'bank'), 'Sandbox connected');
      assert.deepEqual(await shown(page), ['bank'], 'a successful connect stays on the step so the status can be read');
      await stepBox(page, 'bank').getByText('Sandbox company', { exact: true }).waitFor();
      await page.locator('#scout-onboarding-disconnect-bank').click();
      await page.waitForFunction(() => document.querySelector('#scout-onboarding-status-bank')?.textContent.includes('Not started'));
      assert.ok(server.calls.some(call => call.method === 'DELETE' && call.path === '/api/onboarding/connect/bunq'));
      await page.locator('#scout-onboarding-action-bank').click();
      await page.waitForFunction(() => document.querySelector('#scout-onboarding-status-bank')?.textContent.includes('Sandbox connected'));
      await advance(page, 'invoicing');
      assert.equal(server.calls.filter(call => call.method === 'PATCH').length, 0, 'Next on a connected step writes nothing');
      await page.locator('#scout-onboarding-action-invoicing').click();
      await page.waitForFunction(() => document.querySelector('#scout-onboarding-status-invoicing')?.textContent.trim() === 'Connected');
      await advance(page, 'ksef');
      // KSeF: NIP is validated before any request; an unsupported test setup is a prerequisite, not "connected".
      await page.locator('#scout-onboarding-action-ksef').click();
      await page.locator('#scout-onboarding-ksef-nip').fill('123');
      await page.locator('#scout-onboarding-ksef-submit').click();
      await page.locator('#scout-onboarding-ksef-nip-error').waitFor();
      assert.equal(server.calls.filter(call => call.path.endsWith('/ksef')).length, 0);
      await page.getByLabel('Company NIP', { exact: true }).fill('5252248481');
      await page.locator('#scout-onboarding-ksef-submit').click();
      await page.waitForFunction(() => document.querySelector('#scout-onboarding-status-ksef')?.textContent.includes('Needs setup'));
      await stepBox(page, 'ksef').getByText('Test-environment authentication is not configured on this server.').waitFor();
      assert.deepEqual(server.calls.find(call => call.path.endsWith('/ksef')).body, { nip: '5252248481' });
      // Step forms never leak into the next step, and returning starts from a clean form.
      await page.locator('#scout-onboarding-ksef-nip').fill('999');
      await advance(page, 'email'); await page.locator('#scout-onboarding-back').click(); await page.locator('#scout-onboarding-step-ksef').waitFor();
      assert.equal(await page.locator('#scout-onboarding-ksef-nip').count(), 0, 'form state is reset when a step is re-entered');
      assert.deepEqual(page.errors, []); await page.close(); }

    // 5. Per-step skip proceeds; skipped steps can be resumed through Back; resume lands on the first unsettled step.
    { const { page, server } = await open(fresh(), '#setup');
      await page.locator('#scout-onboarding-skip-bank').click();
      await page.locator('#scout-onboarding-step-invoicing').waitFor();
      assert.deepEqual(server.calls.find(call => call.method === 'PATCH').body, { revision: 1, action: 'step', step: 'bank', state: 'skipped' });
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'scout-onboarding-title-invoicing', 'focus moves to the new step heading');
      await page.locator('#scout-onboarding-back').click(); await page.locator('#scout-onboarding-step-bank').waitFor();
      assert.equal(await status(page, 'bank'), 'Skipped');
      await page.locator('#scout-onboarding-resume-bank').click();
      await page.waitForFunction(() => document.querySelector('#scout-onboarding-status-bank')?.textContent.includes('Not started'));
      assert.deepEqual(await shown(page), ['bank'], 'Set up stays on the step');
      await page.reload({ waitUntil: 'commit' }); await page.locator('#scout-onboarding').waitFor();
      await page.locator('#scout-onboarding-step-bank').waitFor(); await page.close(); }
    { const { page } = await open(settledFrom(fresh('in_progress'), ['bank', 'invoicing', 'ksef']), '#setup');
      await page.locator('#scout-onboarding-step-email').waitFor();
      assert.equal(await page.locator('#scout-onboarding-progress-label').innerText(), 'Step 4 of 6', 'resumes at the first unsettled step'); await page.close(); }
    // A failed save never advances; Next on an untouched optional step records an explicit skip first.
    { const { page, server } = await open(fresh(), '#setup', { failPatch: true });
      await page.locator('#scout-onboarding-next').click();
      await page.getByRole('alert').filter({ hasText: 'Setup could not be saved' }).waitFor();
      assert.deepEqual(await shown(page), ['bank']);
      await page.locator('#scout-onboarding-skip-bank').click();
      await page.getByRole('alert').filter({ hasText: 'Setup could not be saved' }).waitFor();
      assert.deepEqual(await shown(page), ['bank']);
      await page.getByText('Next skips this step.', { exact: false }).waitFor();
      server.failPatch = false;
      await page.locator('#scout-onboarding-next').click(); await page.locator('#scout-onboarding-step-invoicing').waitFor();
      assert.deepEqual(server.calls.filter(call => call.method === 'PATCH').at(-1).body, { revision: 1, action: 'step', step: 'bank', state: 'skipped' });
      await page.close(); }

    // 6. Google really starts OAuth: POST with no client-side proof/redirect, then navigate to the returned URL.
    { const { page, server } = await open(settledFrom(fresh(), ['bank', 'invoicing', 'ksef']), '#setup');
      await page.route('**/__mock-oauth/**', route => route.fulfill({ status: 200, contentType: 'text/html', body: '<h1 id="mock-consent">Mock consent</h1>' }));
      await page.getByRole('button', { name: 'Continue with Google', exact: true }).click();
      await page.locator('#mock-consent').waitFor();
      assert.match(page.url(), /\/__mock-oauth\/google$/);
      assert.deepEqual(server.calls.find(call => call.path.endsWith('/connect/google')).body, { label: 'INBOX' });
      await page.close(); }
    { const { page } = await open(fresh(), '#setup', { failProvider: 'google' });
      await walkTo(page, 'email');
      await page.getByRole('button', { name: 'Continue with Google', exact: true }).click();
      await page.getByRole('alert').filter({ hasText: 'google is unavailable' }).waitFor();
      assert.match(page.url(), /#setup$/); assert.equal(await status(page, 'email'), 'Not started'); assert.deepEqual(await shown(page), ['email']); await page.close(); }
    // Missing app credentials come back as a 200 Setup prerequisite: shown as such, no navigation, never "connected".
    { const { page } = await open(fresh(), '#setup', { missingCredentials: 'google' });
      await walkTo(page, 'email');
      await page.getByRole('button', { name: 'Continue with Google', exact: true }).click();
      await page.waitForFunction(() => document.querySelector('#scout-onboarding-status-email')?.textContent.includes('Needs setup'));
      await stepBox(page, 'email').getByText('Google mailbox credentials are not configured on this server.').waitFor();
      assert.match(page.url(), /#setup$/); await page.close(); }
    // The callback redirect lands on the provider's step, reports an outcome once and removes it from the URL; state still comes from the server.
    { const { page } = await open(fresh('in_progress'), '?setup=google-failed#setup');
      await page.getByRole('alert').filter({ hasText: 'Google connection did not complete' }).waitFor();
      await page.locator('#scout-onboarding-step-email').waitFor();
      assert.deepEqual(await shown(page), ['email'], 'OAuth return lands on the email step although earlier steps are unsettled');
      assert.equal(new URL(page.url()).searchParams.has('setup'), false); await page.close(); }
    { const { page } = await open(fresh('in_progress'), '?setup=slack-connected#setup');
      await page.getByText('Slack authorization completed.', { exact: false }).waitFor();
      await page.locator('#scout-onboarding-step-slack').waitFor();
      assert.equal(await page.locator('#scout-onboarding-progress-label').innerText(), 'Step 5 of 6');
      assert.equal(new URL(page.url()).searchParams.has('setup'), false); await page.close(); }

    // When one-click joining found no channel, the manual channel list is open; once connected it folds away.
    { const initial = settledFrom(fresh('in_progress'), ['bank', 'invoicing', 'ksef', 'email']); initial.steps = initial.steps.map(s => s.id === 'slack' ? step('slack', 'needs_configuration', { provider: 'slack', display: { authorized: true, workspace: 'Test Slack' } }) : s);
      const { page, server } = await open(initial, '#setup');
      await page.locator('#scout-onboarding-slack-channels-form').waitFor();
      assert.equal(await status(page, 'slack'), 'Needs setup');
      assert.equal(await page.locator('#scout-onboarding-action-slack').count(), 0);
      await page.locator('#scout-onboarding-slack-channels').fill('general');
      await page.locator('#scout-onboarding-slack-channels-save').click();
      await page.locator('#scout-onboarding-slack-channels-error').waitFor();
      assert.equal(server.calls.filter(c => c.method === 'PUT').length, 0);
      await page.locator('#scout-onboarding-slack-channels').fill('C0123456789, G0123456789');
      await page.locator('#scout-onboarding-slack-channels-save').click();
      await page.waitForFunction(() => document.querySelector('#scout-onboarding-status-slack')?.textContent.trim() === 'Connected');
      assert.deepEqual(server.calls.find(c => c.method === 'PUT').body, { channels: ['C0123456789', 'G0123456789'] });
      assert.deepEqual(await shown(page), ['slack'], 'saving channels stays on the step');
      assert.equal(await page.locator('#scout-onboarding-slack-channels').isVisible(), false, 'manual list folds away once connected');
      await page.locator('#scout-onboarding-slack-manual summary').click();
      await page.locator('#scout-onboarding-slack-channels').fill('');
      await page.locator('#scout-onboarding-slack-channels-save').click();
      await page.waitForFunction(() => document.querySelector('#scout-onboarding-status-slack')?.textContent.trim() === 'Needs setup');
      await page.locator('#scout-onboarding-disconnect-slack').click();
      await page.waitForFunction(() => document.querySelector('#scout-onboarding-status-slack')?.textContent.trim() === 'Not started');
      assert.equal(await page.locator('#scout-onboarding-slack-channels-form').count(), 0); await page.close(); }
    { const initial = settledFrom(fresh('in_progress'), ['bank', 'invoicing', 'ksef']); initial.steps = initial.steps.map(s => s.id === 'email' ? step('email', 'connecting', { provider: 'google' }) : s);
      const { page, server } = await open(initial, '#setup');
      await page.locator('#scout-onboarding-cancel-email').click();
      await page.waitForFunction(() => document.querySelector('#scout-onboarding-status-email')?.textContent.trim() === 'Not started');
      assert.ok(server.calls.some(c => c.method === 'DELETE' && c.path.endsWith('/connect/google'))); await page.close(); }

    // 7. Company network: conditional questions, consent, no secrets, non-secret answers recorded.
    { const { page, server } = await open(settledFrom(fresh('in_progress'), ['bank', 'invoicing', 'ksef', 'email', 'slack']), '#setup');
      await page.locator('#scout-onboarding-action-network').click();
      assert.equal(await page.locator('#scout-onboarding-network-provider').count(), 0, 'questions are conditional');
      await page.getByLabel('Yes', { exact: true }).check();
      await page.locator('#scout-onboarding-network-submit').click();
      for (const field of ['provider', 'protocol', 'administrator', 'systems', 'company-only']) await page.locator(`#scout-onboarding-network-${field}-error`).waitFor();
      assert.equal(server.calls.filter(call => call.method === 'PATCH').length, 0);
      assert.equal(await page.locator('#scout-onboarding-network-form input[type="file"], #scout-onboarding-network-form input[type="password"]').count(), 0, 'no secret or file fields');
      await page.getByLabel('VPN provider', { exact: true }).fill('-----BEGIN PRIVATE KEY-----');
      await page.getByLabel('Protocol', { exact: true }).fill('UDP');
      await page.getByLabel('Responsible administrator', { exact: true }).fill('IT team');
      await page.getByLabel('Systems Scout needs to reach', { exact: true }).fill('ERP, File server');
      await page.getByLabel('I approve access to company folders and company mail only, never personal files.').check();
      await page.locator('#scout-onboarding-network-submit').click();
      await page.locator('#scout-onboarding-network-provider-error').waitFor();
      await page.getByLabel('VPN provider', { exact: true }).fill('WireGuard');
      await page.locator('#scout-onboarding-network-submit').click();
      await page.waitForFunction(() => document.querySelector('#scout-onboarding-status-network')?.textContent.includes('Administrator setup required'));
      assert.deepEqual(server.calls.find(call => call.method === 'PATCH').body, { revision: 1, action: 'network', network: { required: true, provider: 'WireGuard', protocol: 'UDP', administrator: 'IT team', systems: ['ERP', 'File server'], company_only: true } });
      await page.close(); }

    // 8. Conflict: the latest state is reloaded and the user is told; nothing is silently discarded and the wizard does not advance.
    { const { page, server } = await open(fresh(), '#setup', { conflictOnce: true });
      await page.locator('#scout-onboarding-skip-bank').click();
      await page.getByRole('alert').filter({ hasText: 'changed in another session' }).waitFor();
      assert.equal(await status(page, 'bank'), 'Not started');
      assert.deepEqual(await shown(page), ['bank']);
      await page.locator('#scout-onboarding-skip-bank').click();
      await page.locator('#scout-onboarding-step-invoicing').waitFor();
      assert.equal(server.calls.filter(call => call.method === 'PATCH').at(-1).body.revision, 6); await page.close(); }

    // 9. Leaving: skip-all and the final Finish persist first, then navigate; a failed save keeps the user in setup.
    { const { page, server } = await open(fresh(), '#setup', { failPatch: true });
      await page.locator('#scout-onboarding-skip').click();
      await page.getByRole('alert').filter({ hasText: 'Setup could not be saved' }).waitFor();
      assert.equal(await page.locator('#scout-onboarding').count(), 1); assert.match(page.url(), /#setup$/);
      server.failPatch = false;
      await page.locator('#scout-onboarding-skip').click();
      await page.locator('#scout-chat-input').waitFor({ state: 'visible' });
      assert.equal(server.setup.status, 'dismissed'); await page.close(); }
    { const { page, server } = await open(settledFrom(fresh(), ['bank', 'invoicing', 'ksef', 'email', 'slack']), '#setup', { failPatch: true });
      await page.locator('#scout-onboarding-step-network').waitFor();
      await page.getByRole('button', { name: 'Finish setup', exact: true }).click();
      await page.getByRole('alert').filter({ hasText: 'Setup could not be saved' }).waitFor();
      assert.equal(await page.locator('#scout-onboarding').count(), 1, 'a failed final save does not leave setup');
      server.failPatch = false;
      await page.getByRole('button', { name: 'Finish setup', exact: true }).click();
      await page.locator('#scout-chat-input').waitFor({ state: 'visible' });
      assert.equal(server.setup.status, 'completed', 'skipping the last step then finishing completes truthfully');
      assert.equal(server.setup.steps.find(entry => entry.id === 'network').state, 'skipped');
      await page.reload({ waitUntil: 'commit' }); await page.locator('#scout-chat-input').waitFor({ state: 'visible' }); await page.waitForTimeout(300);
      assert.equal(await page.locator('#scout-onboarding').count(), 0, 'completion survives reload'); await page.close(); }
    { const initial = settledFrom(fresh(), ['bank', 'invoicing', 'ksef', 'email', 'slack']); initial.steps = initial.steps.map(s => s.id === 'network' ? step('network', 'needs_configuration') : s);
      const { page, server } = await open(initial, '#setup');
      await page.getByRole('button', { name: 'Finish setup', exact: true }).click();
      await page.locator('#scout-chat-input').waitFor({ state: 'visible' });
      assert.equal(server.setup.status, 'dismissed', 'an unresolved prerequisite dismisses instead of completing');
      assert.equal(server.setup.steps.find(entry => entry.id === 'network').state, 'needs_configuration', 'saved administrator-setup state is not overwritten');
      await page.close(); }
    { const done = fresh(); done.steps = IDS.map(id => step(id, 'skipped'));
      const { page, server } = await open(done, '#setup');
      await page.getByRole('button', { name: 'Finish setup', exact: true }).click();
      await page.locator('#scout-chat-input').waitFor({ state: 'visible' });
      assert.equal(server.setup.status, 'completed'); await page.close(); }
    { const { page, server } = await open(fresh(), '#setup');
      await walkTo(page, 'network');
      await page.locator('#scout-onboarding-skip-network').click();
      await page.locator('#scout-chat-input').waitFor({ state: 'visible' });
      assert.equal(server.setup.status, 'completed', 'skipping the final step proceeds out of setup'); await page.close(); }

    // 10. Reopen from Connections without a seventh row.
    { const { page } = await open(fresh('dismissed'), '#connections');
      await page.getByRole('heading', { name: 'Connections', exact: true }).waitFor();
      assert.equal(await page.locator('[data-connection-row]').count(), 6);
      await page.locator('#scout-onboarding-reopen').click();
      await page.locator('#scout-onboarding').waitFor(); assert.match(page.url(), /#setup$/); await page.close(); }

    // 11. Agent handoff: exact CTA, agent-neutral dialog, copy/download, clipboard failure fallback, optional Claude shortcut.
    { const { page, server } = await open(fresh(), '#setup');
      await page.getByRole('button', { name: 'Continue with your agent', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: 'Continue with your agent' });
      await dialog.getByRole('button', { name: 'Copy instructions', exact: true }).waitFor();
      assert.equal(server.calls.filter(call => call.path.endsWith('/handoff')).length, 1);
      await dialog.getByText('Codex or Claude Code', { exact: false }).waitFor();
      await dialog.getByRole('button', { name: 'Copy setup link', exact: true }).click();
      assert.match(await page.evaluate(() => navigator.clipboard.readText()), /#setup$/);
      await dialog.getByRole('button', { name: 'Copy instructions', exact: true }).click();
      assert.match(await page.evaluate(() => navigator.clipboard.readText()), /^Help me finish Scout setup/);
      await dialog.getByText('Instructions copied.', { exact: true }).waitFor();
      const [markdown] = await Promise.all([page.waitForEvent('download'), dialog.getByRole('button', { name: 'Download Markdown', exact: true }).click()]);
      assert.equal(markdown.suggestedFilename(), 'scout-setup.md');
      const [manifest] = await Promise.all([page.waitForEvent('download'), dialog.getByRole('button', { name: 'Download JSON', exact: true }).click()]);
      assert.equal(manifest.suggestedFilename(), 'scout-setup.json');
      assert.equal(JSON.parse(fs.readFileSync(await manifest.path(), 'utf8')).version, 'scout.setup/v1');
      const claude = dialog.getByRole('link', { name: 'open in Claude Code' });
      assert.equal(await claude.getAttribute('href'), 'claude-cli://open?q=hello');
      await page.evaluate(() => { Object.defineProperty(navigator, 'clipboard', { value: { writeText: () => Promise.reject(new Error('denied')) }, configurable: true }); });
      await dialog.getByRole('button', { name: 'Copy instructions', exact: true }).click();
      const fallback = dialog.getByLabel('Text to copy manually');
      await fallback.waitFor(); assert.match(await fallback.inputValue(), /^Help me finish Scout setup/);
      await dialog.getByText('Your browser blocked copying', { exact: false }).waitFor();
      await page.screenshot({ path: path.join(output, 'handoff-dialog.png') });
      await page.keyboard.press('Escape');
      await dialog.waitFor({ state: 'hidden' });
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'scout-onboarding-agent', 'focus returns to the trigger');
      await page.close(); }
    { const { page } = await open(fresh(), '#setup');
      await page.route('**/api/onboarding/handoff', route => route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"Handoff failed"}' }));
      await page.locator('#scout-onboarding-agent').click();
      await page.getByRole('alert').filter({ hasText: 'Handoff failed' }).waitFor(); await page.close(); }

    // 12. Keyboard: the wizard is reachable and activated without a pointer; focus is visible and follows the step.
    { const { page, server } = await open(fresh(), '#setup');
      await page.locator('#scout-onboarding-action-bank').focus();
      assert.notEqual(await page.locator('#scout-onboarding-action-bank').evaluate(node => getComputedStyle(node).outlineStyle + getComputedStyle(node).boxShadow), 'none none');
      await page.keyboard.press('Tab'); // skip bank
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'scout-onboarding-skip-bank');
      await page.keyboard.press('Enter');
      await page.locator('#scout-onboarding-step-invoicing').waitFor();
      assert.equal(server.calls.filter(call => call.method === 'PATCH').length, 1);
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'scout-onboarding-title-invoicing');
      await page.locator('#scout-onboarding-back').focus(); await page.keyboard.press('Enter');
      await page.locator('#scout-onboarding-step-bank').waitFor();
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'scout-onboarding-title-bank');
      await page.locator('#scout-onboarding-next').focus(); await page.keyboard.press('Enter');
      await page.locator('#scout-onboarding-step-invoicing').waitFor();
      await page.close(); }

    // 13. Non-owners see progress read-only, can page through every step and cannot change or hand off anything.
    { const member = patient(await authenticatedFixture(browser));
      const slug = member.slug;
      await pool.query(`UPDATE scout_auth.memberships SET role='member' WHERE organization_id=(SELECT id FROM scout_auth.organizations WHERE slug=$1)`, [slug]);
      const page = await member.newPage({ viewport: { width: 1440, height: 1000 } });
      const server = await mockServer(page, { ...fresh(), steps: IDS.map(id => step(id, id === 'bank' ? 'sandbox_connected' : 'not_started')) });
      await page.goto(member.base + '/#setup', { waitUntil: 'commit' });
      await page.locator('#scout-onboarding-readonly').waitFor();
      assert.deepEqual(await shown(page), ['invoicing'], 'resume skips the already connected bank');
      await page.locator('#scout-onboarding-back').click(); await page.locator('#scout-onboarding-step-bank').waitFor();
      assert.equal(await status(page, 'bank'), 'Sandbox connected');
      assert.equal(await page.locator('#scout-onboarding [id^="scout-onboarding-action-"], #scout-onboarding [id^="scout-onboarding-skip"], #scout-onboarding-agent').count(), 0);
      await walkTo(page, 'network');
      await page.getByRole('button', { name: 'Finish setup', exact: true }).click();
      await page.locator('#scout-chat-input').waitFor({ state: 'visible' });
      assert.equal(server.calls.filter(call => call.method !== 'GET').length, 0, 'members never write');
      // And a member's ordinary load never auto-opens setup.
      await page.goto(member.base + '/', { waitUntil: 'commit' }); await page.locator('#scout-chat-input').waitFor({ state: 'visible' }); await page.waitForTimeout(300);
      assert.equal(await page.locator('#scout-onboarding').count(), 0); await page.close(); }

    console.log('PASS onboarding UI (mocked /api/onboarding contract; no provider contacted): entry rules, sequential Step N of 6, truthful states, errors/409 never advance, skip/resume, Google start, OAuth return step, network form, handoff, reopen, keyboard, read-only, responsive light/dark');
  } finally { await pool.end(); await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
