// Local authenticated browser fixture; provider and Granite responses are explicitly mocked.
const { chromium } = require('playwright');
const { authenticatedFixture } = require('./authenticated-browser.cjs');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const output = process.env.SCREENSHOT_DIR || '/tmp/scout-external-provider-screens';
const testKey = 'test-key-for-browser-fixture';

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH });
  try {
    const fixture = await authenticatedFixture(browser);
    for (const theme of ['light', 'dark']) for (const viewport of [{ width: 1440, height: 1000 }, { width: 375, height: 812 }, { width: 812, height: 375 }]) {
      const page = await fixture.newPage({ viewport, reducedMotion: 'reduce' });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(theme => localStorage.setItem('scout-theme', theme), theme);
      await page.goto(`${fixture.base}#chat`);
      await page.locator('#scout-chat-model').click();
      await page.locator('#scout-chat-configure-provider').click();
      await page.getByRole('dialog').waitFor();
      await page.locator('#scout-external-key').fill(testKey);
      assert.equal(await page.locator('#scout-external-key').getAttribute('type'), 'password');
      await page.locator('#scout-external-provider').selectOption('mistral');
      assert.equal(await page.locator('#scout-external-key').inputValue(), '', 'Switching provider clears old key');
      assert.equal(await page.locator('#scout-external-model').inputValue(), 'mistral-small-latest');
      await page.locator('#scout-external-key').fill(testKey);
      await page.locator('#scout-external-model').fill('bad model id');
      await page.locator('#scout-external-save').click();
      await page.locator('#scout-external-error').waitFor();
      await page.locator('#scout-external-model').fill('mistral-small-latest');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      const dialog = await page.getByRole('dialog').boundingBox();
      assert.ok(dialog.x >= 0 && dialog.x + dialog.width <= viewport.width && dialog.y >= 0 && dialog.y + dialog.height <= viewport.height, 'Dialog fits viewport');
      await page.screenshot({ path: `${output}/setup-${theme}-${viewport.width}.png` });
      await page.locator('#scout-external-save').click();
      await page.getByRole('dialog').waitFor({ state: 'hidden' });
      assert.match(await page.locator('#scout-chat-model').textContent(), /Mistral/);
      const requests = [];
      await page.route('**/api/chat', async route => {
        requests.push(route.request().postDataJSON());
        await route.fulfill({ contentType: 'application/x-ndjson', body: [
          { type: 'step', text: 'Granite started a read-only forecast on premise' },
          { type: 'delta', text: 'Private forecast result: €1234.56' },
          { type: 'done', truncated: false, task_id: 'SC-42' },
        ].map(JSON.stringify).join('\n') + '\n' });
      });
      await page.locator('#scout-chat-input').fill('Forecast cash for 90 days');
      await page.locator('#scout-chat-input').press('Enter');
      await page.getByText('Private forecast result: €1234.56', { exact: true }).waitFor();
      await page.locator('#scout-chat-input').fill('Forecast cash for 30 days');
      await page.locator('#scout-chat-input').press('Enter');
      await page.waitForFunction(() => document.querySelectorAll('#scout-chat-messages > li').length === 4);
      assert.equal(requests.length, 2);
      for (const request of requests) { assert.equal(request.model, 'external'); assert.equal(request.external.apiKey, testKey); assert.equal(request.messages.length, 1); assert.doesNotMatch(JSON.stringify(request.messages), /Private forecast/); }
      assert.equal(await page.evaluate(key => JSON.stringify(localStorage).includes(key) || JSON.stringify(sessionStorage).includes(key), testKey), false, 'Key is never persisted');
      await page.screenshot({ path: `${output}/forecast-${theme}-${viewport.width}.png` });
      await page.locator('#scout-chat-model').click();
      await page.locator('#scout-chat-model-modelcar-granite-4-0-h-tiny').click();
      assert.equal(await page.locator('#scout-chat-messages').count(), 0, 'Switching models starts fresh');
      await page.locator('#scout-chat-model').click();
      await page.locator('#scout-chat-configure-provider').click();
      await page.locator('#scout-external-remove').click();
      await page.locator('#scout-chat-model').click();
      assert.equal(await page.locator('#scout-chat-model-external').count(), 0);
      await page.keyboard.press('Escape');
      await page.reload();
      await page.locator('#scout-chat-model').click();
      await page.locator('#scout-chat-configure-provider').click();
      assert.equal(await page.locator('#scout-external-key').inputValue(), '');
      assert.deepEqual(errors, []);
      await page.close();
      console.log(`PASS external setup, isolated messages, removal/reload: ${theme} ${viewport.width}`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
