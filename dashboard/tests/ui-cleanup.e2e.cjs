// Run against a local DASHBOARD_PREVIEW=1 server after `npx playwright install chromium`.
// BASE_URL=http://127.0.0.1:3000 npm run test:e2e
// CHROMIUM_PATH and PLAYWRIGHT_MODULE optionally use preinstalled browser tooling.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.BASE_URL || 'http://127.0.0.1:3000';
const output = process.env.SCREENSHOT_DIR || path.resolve(__dirname, '../../artifacts/ui-cleanup');

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  try {
    for (const theme of ['light', 'dark']) {
      const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('console', message => { if (message.type() === 'error' && /hydrat/i.test(message.text())) errors.push(message.text()); });
      await page.addInitScript(theme => localStorage.setItem('scout-theme', theme), theme);
      await page.goto(`${base}/#chat`);
      await page.locator('#scout-chat-input').waitFor();
      await page.waitForFunction(theme => document.documentElement.classList.contains('dark') === (theme === 'dark'), theme);
      const labels = await page.locator('#scout-sidebar [data-sidebar="menu-button"] a, #scout-sidebar a[data-sidebar="menu-button"]').allTextContents();
      assert.deepEqual(labels.map(text => text.replace(/\d+/g, '').trim()).slice(0, 4), ['Overview', 'Scout chat', 'Tasks', 'Transactions']);
      assert.equal(await page.locator('#scout-nav-inbox, #scout-nav-demo, #scout-nav-staffing').count(), 0);
      assert.equal(await page.getByRole('button', { name: 'Finance', exact: true }).count(), 0);
      const chatStyle = await page.locator('#scout-llm-chat').evaluate(element => {
        const style = getComputedStyle(element);
        return { border: style.borderTopWidth, background: style.backgroundColor };
      });
      assert.equal(chatStyle.border, '0px', 'Chat should not be enclosed by a card border');
      assert.equal(chatStyle.background, 'rgba(0, 0, 0, 0)', 'Chat should use the page surface');
      assert.equal(await page.getByRole('heading', { name: 'Scout chat', exact: true }).count(), 0, 'No duplicate chat header');
      await page.getByRole('heading', { name: 'Ask Scout', exact: true }).waitFor();
      const newBox = await page.locator('#scout-chat-new').boundingBox();
      const chatBox = await page.locator('#scout-llm-chat').boundingBox();
      assert.ok(Math.abs(newBox.x + newBox.width - chatBox.x - chatBox.width) < 10, 'New chat belongs at top right');
      await page.locator('#scout-chat-suggestion-0').click();
      assert.equal(await page.locator('#scout-chat-input').inputValue(), 'Which payments are missing invoices?');
      await page.locator('#scout-chat-new').click();
      assert.equal(await page.locator('#scout-chat-input').inputValue(), '', 'New chat clears draft');
      await page.screenshot({ path: path.join(output, `chat-${theme}.png`) });

      // Exercise client streaming, navigation persistence and reset without contacting a live model.
      await page.route('**/api/chat', route => route.fulfill({ status: 200, contentType: 'application/x-ndjson', body: [
        JSON.stringify({ type: 'delta', text: 'I found the payments that need invoices.' }),
        JSON.stringify({ type: 'done', truncated: false }),
      ].join('\n') + '\n' }));
      await page.locator('#scout-chat-input').fill('Check invoices');
      await page.locator('#scout-chat-input').press('Enter');
      await page.getByText('I found the payments that need invoices.', { exact: true }).waitFor();
      assert.equal(await page.getByRole('heading', { level: 1, name: 'Scout chat', exact: true }).count(), 1, 'Active conversations keep an accessible page heading');
      await page.locator('#scout-nav-tasks').click();
      await page.locator('#scout-nav-chat').click();
      await page.getByText('I found the payments that need invoices.', { exact: true }).waitFor();
      await page.locator('#scout-chat-new').click();
      await page.getByRole('heading', { name: 'Ask Scout', exact: true }).waitFor();
      assert.equal(await page.locator('#scout-chat-messages').count(), 0);

      await page.locator('#scout-nav-llm-usage').click();
      await page.locator('#scout-llm-requests svg').first().waitFor();
      const metrics = page.locator('[id^="scout-llm-metric-"]');
      assert.equal(await metrics.count(), 4);
      for (const metric of await metrics.all()) {
        const icon = metric.locator('svg').first();
        assert.equal(await icon.evaluate(element => getComputedStyle(element).width), '16px');
        assert.equal(await icon.evaluate(element => getComputedStyle(element).color), await metric.locator('p').first().evaluate(element => getComputedStyle(element).color));
      }
      const tickFill = await page.locator('#scout-llm-requests svg text').first().evaluate(element => getComputedStyle(element).fill);
      const labelColor = await metrics.first().locator('p').first().evaluate(element => getComputedStyle(element).color);
      assert.equal(tickFill, labelColor, 'Chart axes use theme text tokens in both themes');
      await page.locator('#scout-llm-usage-range-1').click();
      await page.getByText('Per hour, last 24 hours', { exact: true }).waitFor();
      await page.locator('#scout-llm-usage-range-30').click();
      await page.getByText('Per day, last 30 days', { exact: true }).waitFor();
      await page.locator('#scout-llm-usage-range-7').click();
      await page.getByText('Per day, last 7 days', { exact: true }).waitFor();
      await page.screenshot({ path: path.join(output, `usage-${theme}.png`), fullPage: true });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'No desktop horizontal overflow');

      for (const viewport of [{ width: 375, height: 812 }, { width: 812, height: 375 }]) {
        await page.setViewportSize(viewport);
        await page.screenshot({ path: path.join(output, `usage-${theme}-${viewport.width}.png`), fullPage: true });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'No usage horizontal overflow');
        await page.evaluate(() => { location.hash = '#chat'; });
        await page.locator('#scout-chat-input').waitFor({ state: 'visible' });
        await page.locator('#scout-chat-input').fill('Keyboard\nmessage');
        await page.locator('#scout-chat-new').click();
        await page.locator('#scout-chat-conversation').evaluate(element => { element.scrollTop = 0; });
        const headingBox = await page.getByRole('heading', { name: 'Ask Scout', exact: true }).boundingBox();
        const conversationBox = await page.locator('#scout-chat-conversation').boundingBox();
        assert.ok(headingBox.y >= conversationBox.y, 'The welcome heading must not be clipped above the scroll region');
        const longestPrompt = page.locator('#scout-chat-suggestion-1');
        assert.ok(await longestPrompt.evaluate(element => element.scrollWidth <= element.clientWidth + 1 && element.getBoundingClientRect().width <= element.parentElement.clientWidth), 'Long prompts wrap instead of clipping');
        await page.screenshot({ path: path.join(output, `chat-${theme}-${viewport.width}.png`), fullPage: true });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'No chat horizontal overflow');
        const inputBox = await page.locator('#scout-chat-input').boundingBox();
        const mobileNav = await page.locator('#scout-mobile-navigation').isVisible() ? await page.locator('#scout-mobile-navigation').boundingBox() : null;
        assert.ok(inputBox.y >= 0 && inputBox.y + inputBox.height <= (mobileNav?.y ?? viewport.height), 'Composer stays visible above navigation');
        await page.evaluate(() => { location.hash = '#llm-usage'; });
        await page.locator('#scout-llm-usage').waitFor({ state: 'visible' });
      }
      assert.deepEqual(errors, [], 'No browser runtime errors');
      console.log(`PASS ${theme}: sidebar, chat, streaming fixture, reset, usage ranges, desktop, mobile and landscape`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
