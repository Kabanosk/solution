// Focused UI-only test: use an existing isolated local Next server. All auth writes
// and Google navigation are intercepted; no database or real OAuth calls are made.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const base = process.env.BASE_URL || 'http://127.0.0.1:3017';
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw new Error('A local test server is required');

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH });
  try {
    const page = await browser.newPage({ viewport: { width: 375, height: 812 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const requests = [];
    let answer;
    await page.route(`${base}/api/auth/**`, async route => {
      requests.push({ url: route.request().url(), method: route.request().method(), body: route.request().postDataJSON(), origin: route.request().headers().origin });
      await new Promise(resolve => { answer = resolve; });
      await route.fulfill({ status: 409, json: { error: 'Choose another organization URL.' } });
    });
    await page.goto(base);
    await page.getByRole('heading', { name: 'Your organization, in focus.' }).waitFor();
    await page.getByRole('button', { name: 'Create organization', exact: true }).click();
    await page.getByRole('button', { name: 'Create with Google', exact: true }).waitFor({ timeout: 5000 });
    const google = page.locator('#scout-google-signup');
    assert.equal(await google.getAttribute('data-variant'), 'outline');
    assert.equal(await google.locator('img').getAttribute('src'), '/google-g.png');
    assert.equal(await google.locator('img').getAttribute('alt'), '');

    if (process.env.GOOGLE_UI_UNAVAILABLE === '1') {
      assert.equal(await google.isDisabled(), true);
      await page.getByText('Google sign-up hasn’t been configured by your administrator yet.').waitFor();
    } else {
      assert.equal(await google.isEnabled(), true);
      await google.click();
      assert.equal(await page.locator('#scout-name-input').evaluate(node => node === document.activeElement), true);
      assert.deepEqual(requests, []);
      await page.getByLabel('Organization name', { exact: true }).fill('Google Studio');
      assert.equal(await page.getByLabel('Organization URL', { exact: true }).inputValue(), 'google-studio');
      await page.getByLabel('Organization URL', { exact: true }).fill('bad--slug');
      await google.click();
      assert.equal(await page.locator('#scout-slug-input').evaluate(node => node === document.activeElement), true);
      assert.deepEqual(requests, []);
      await page.getByLabel('Organization URL', { exact: true }).fill('google-studio');
      await google.click();
      await page.waitForFunction(() => document.querySelector('#scout-auth-form').getAttribute('aria-busy') === 'true');
      assert.equal(await google.isDisabled(), true);
      assert.equal(await page.locator('#scout-auth-submit').isDisabled(), true);
      assert.equal(await page.getByLabel('Your name', { exact: true }).inputValue(), '');
      assert.equal(await page.getByLabel('Email', { exact: true }).inputValue(), '');
      assert.equal(await page.getByLabel('Password', { exact: true }).inputValue(), '');
      assert.deepEqual(requests, [{ url: `${base}/api/auth/google/start`, method: 'POST', body: { intent: 'register', name: 'Google Studio', slug: 'google-studio' }, origin: base }]);
      answer();
      await page.getByRole('alert').filter({ hasText: 'Choose another organization URL.' }).waitFor();
      await page.waitForFunction(() => document.activeElement?.id === 'scout-auth-error');
      assert.equal(await page.getByRole('button', { name: 'Create with Google', exact: true }).isEnabled(), true);

      // Ordinary password signup keeps its existing payload and full form validation.
      await page.getByLabel('Your name', { exact: true }).fill('Password Owner');
      await page.getByLabel('Email', { exact: true }).fill('owner@example.test');
      await page.getByLabel('Password', { exact: true }).fill('local test password');
      await Promise.all([
        page.waitForRequest(request => request.url() === `${base}/api/auth/register`),
        page.locator('#scout-auth-submit').click(),
      ]);
      assert.deepEqual(requests[1]?.body, { name: 'Google Studio', slug: 'google-studio', ownerName: 'Password Owner', email: 'owner@example.test', password: 'local test password' });
      assert.equal(requests[1]?.url, `${base}/api/auth/register`);
      answer();
      await page.getByRole('button', { name: 'Create with Google', exact: true }).waitFor();
      await page.waitForFunction(() => document.querySelector('#scout-auth-form').getAttribute('aria-busy') === 'false');

      await page.unroute(`${base}/api/auth/**`);
      await page.route(`${base}/api/auth/google/start`, route => route.fulfill({ json: {} }));
      await page.getByRole('button', { name: 'Create with Google', exact: true }).click();
      await page.getByRole('alert').filter({ hasText: 'Could not start Google sign-up. Please try again.' }).waitFor();
      assert.equal(page.url(), `${base}/`);
      assert.equal(await page.getByRole('button', { name: 'Create with Google', exact: true }).isEnabled(), true);
      await page.unroute(`${base}/api/auth/google/start`);
      const destination = 'https://accounts.google.com/o/oauth2/v2/auth?ui-test=1';
      await page.route(`${base}/api/auth/google/start`, route => route.fulfill({ json: { url: destination } }));
      await page.route('https://accounts.google.com/**', route => route.fulfill({ contentType: 'text/html', body: '<h1>Google navigation intercepted</h1>' }));
      await page.getByRole('button', { name: 'Create with Google', exact: true }).click();
      await page.waitForURL(destination);
    }

    await page.goto(`${base}/?create=1&auth=signup-failed`);
    await page.getByRole('heading', { name: 'Create your organization' }).waitFor();
    const message = await page.locator('#scout-auth-form').getByRole('alert').textContent();
    assert.match(message, /existing account/i);
    assert.match(message, /sign in.*link Google/i);
    assert.match(message, /another organization URL/i);
    await page.locator('#scout-mode-toggle').click();
    await page.getByRole('heading', { name: 'Your organization, in focus.' }).waitFor();
    assert.equal(await page.locator('#scout-auth-form').getByRole('alert').count(), 0);
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => document.documentElement.classList.toggle('dark', theme === 'dark'), theme);
      await page.locator('#scout-mode-toggle').click();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
      assert.equal(await page.locator('#scout-auth-brand').count(), 1);
      assert.equal(await page.getByRole('navigation', { name: 'Legal information' }).count(), 1);
      await page.locator('#scout-mode-toggle').click();
    }
    assert.deepEqual(errors, []);
    console.log(process.env.GOOGLE_UI_UNAVAILABLE === '1'
      ? 'Google signup unavailable state and callback recovery checks passed.'
      : 'Google signup UI validation, pending/error, malformed response, redirect, password fallback and callback recovery checks passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
