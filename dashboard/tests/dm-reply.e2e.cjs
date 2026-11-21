// A colleague's reply to a message Scout sent shows up in the open chat and is acknowledged only while the chat is
// visible. Needs a local authenticated organization server; /api/chat, /api/tasks and /api/chat/ack are controlled, so no
// Scout, model or Slack call is made.
const { chromium } = require('playwright-core');
const { authenticatedFixture } = require("./authenticated-browser.cjs");
let fixture;
const assert = require('node:assert/strict');
let base = process.env.BASE_URL || 'http://127.0.0.1:3000';
const reply = 'Grazyna replied to your message “Is the Asimex invoice filed?”: Yes, filed this morning.';

async function open(browser, { replyAfterMs, failAckCount = 0, hideOnFailure = false }) {
  const page = await fixture.newPage({ viewport: { width: 1280, height: 900 }, reducedMotion: 'reduce' });
  const acks = [];
  let polls = 0;
  const started = Date.now();
  await page.route('**/api/chat', route => route.request().method() === 'POST' ? route.fulfill({ status: 200, contentType: 'application/x-ndjson', body: [
    JSON.stringify({ type: 'step', text: 'Looked at send dm(person=Grazyna): sent' }),
    JSON.stringify({ type: 'delta', text: 'I sent Grazyna your message and will pass her reply on.' }),
    JSON.stringify({ type: 'done', truncated: false, task_id: 'SC-900', followups: true }),
  ].join('\n') + '\n' }) : route.continue());
  await page.route('**/api/tasks/SC-900', route => {
    polls++;
    const steps = Date.now() - started > replyAfterMs ? [{ id: 4242, kind: 'followup', text: reply }] : [];
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ steps }) });
  });
  await page.route('**/api/chat/ack', async route => {
    acks.push(route.request().postDataJSON());
    const failed = acks.length <= failAckCount;
    if (failed && hideOnFailure) await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    return route.fulfill({ status: failed ? 503 : 200, contentType: 'application/json', body: failed ? '{"error":"temporarily unavailable"}' : '{"acknowledged":1}' });
  });
  await page.goto(`${base}/#chat`);
  await page.locator('#scout-chat-input').waitFor();
  await page.locator('#scout-chat-input').fill('Ask Grazyna if the Asimex invoice is filed');
  await page.locator('#scout-chat-input').press('Enter');
  await page.getByText('I sent Grazyna your message and will pass her reply on.', { exact: true }).waitFor();
  return { page, acks, polls: () => polls };
}

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  try {
    fixture = await authenticatedFixture(browser);
    base = fixture.base;
    // 1. The chat is open and visible when the reply arrives: it is shown as an update and acknowledged exactly once.
    const open_ = await open(browser, { replyAfterMs: 0 });
    await open_.page.getByText('filed this morning', { exact: false }).waitFor({ timeout: 15000 });
    await open_.page.waitForTimeout(1500);
    assert.deepEqual(open_.acks, [{ taskId: 'SC-900', stepIds: [4242] }], 'one acknowledgement for the shown reply');
    await open_.page.close();

    // 2. The reply arrives while the user is on another page: shown later, but only acknowledged once the chat is visible again.
    const away = await open(browser, { replyAfterMs: 2500 });
    await away.page.locator('#scout-nav-tasks').click();
    const before = away.polls();
    await away.page.waitForTimeout(7000);
    assert.ok(away.polls() > before, 'the chat keeps polling for the reply');
    assert.deepEqual(away.acks, [], 'a chat nobody is looking at does not acknowledge, so Slack gets the reply');
    await away.page.locator('#scout-nav-chat').click();
    await away.page.getByText('filed this morning', { exact: false }).waitFor();
    await away.page.waitForTimeout(1000);
    assert.deepEqual(away.acks, [{ taskId: 'SC-900', stepIds: [4242] }]);
    await away.page.close();

    // 3. New chat before the reply arrives: the old conversation is gone, so nothing is shown or acknowledged.
    const reset = await open(browser, { replyAfterMs: 2500 });
    await reset.page.locator('#scout-chat-new').click();
    await reset.page.waitForTimeout(7000);
    assert.deepEqual(reset.acks, [], 'a discarded conversation does not acknowledge, so Slack gets the reply');
    assert.equal(await reset.page.getByText('filed this morning', { exact: false }).count(), 0);
    await reset.page.close();
    // 4. A temporary acknowledgement error is retried without requiring a new message or page switch.
    const retry = await open(browser, { replyAfterMs: 0, failAckCount: 1 });
    await retry.page.waitForResponse(response => response.url().endsWith('/api/chat/ack') && response.status() === 200, { timeout: 6500 });
    assert.equal(retry.acks.length, 2, 'transient acknowledgement failure is retried');
    await retry.page.close();

    // 5. Returning to a visible browser tab acknowledges an already-rendered reply, even with no new messages.
    const hidden = await open(browser, { replyAfterMs: 0, failAckCount: 1, hideOnFailure: true });
    await hidden.page.waitForFunction(() => document.hidden);
    await hidden.page.waitForTimeout(1000);
    assert.equal(hidden.acks.length, 1, 'hidden tabs must not retry acknowledgement');
    const acknowledged = hidden.page.waitForResponse(response => response.url().endsWith('/api/chat/ack') && response.status() === 200, { timeout: 6500 });
    await hidden.page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await acknowledged;
    assert.equal(hidden.acks.length, 2);
    await hidden.page.close();
    console.log('dm reply e2e ok: open, away, reset, retry and browser visibility restoration');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
