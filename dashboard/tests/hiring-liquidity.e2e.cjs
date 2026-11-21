// Isolated local database/browser fixtures; no real provider, hiring or company records are used.
const { chromium } = require('playwright');
const { Pool } = require('pg');
const { authenticatedFixture } = require('./authenticated-browser.cjs');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const output = process.env.SCREENSHOT_DIR || '/tmp/scout-hiring-screens';
const title = 'What if we hired 2 people and paid them €3,000.00 each per month?';
const research = { summary: 'Fixture research estimates €2,500–€3,500 monthly gross pay for cooks in Berlin. This is public sample evidence, not company payroll.', sources: [{ title: 'Public employment statistics', url: 'https://www.arbeitsagentur.de/jobsuche/' }], researched_at: '2026-11-21T12:00:00Z' };
const report = '**2 Cook hires** in Berlin at **€3,000 gross per person per month** from December for 6 months.\n\nEmployer cost assumption: 20%. **€7,200 per month**, **€43,200** total. Payroll repeats at month end.\n\nFixture private forecast: balance decreases by €43,200 against the baseline. No employees were hired.\n\nSources: [Public employment statistics](https://www.arbeitsagentur.de/jobsuche/)';

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH });
  try {
    const fixture = await authenticatedFixture(browser);
    const pool = new Pool({ connectionString: process.env.SCOUT_AUTH_TEST_DATABASE_URL });
    let organization, owner;
    try {
      organization = (await pool.query('SELECT id,schema_name FROM scout_auth.organizations WHERE slug=$1', [fixture.slug])).rows[0];
      assert.match(organization.schema_name, /^tenant_[a-f0-9]{32}$/);
      owner = (await pool.query('SELECT employee_id FROM scout_auth.memberships WHERE organization_id=$1 AND role=\'owner\'', [organization.id])).rows[0].employee_id;
    } finally { await pool.end(); }
    async function saveTask(taskId) {
      const writer = new Pool({ connectionString: process.env.SCOUT_AUTH_TEST_DATABASE_URL });
      try {
        await writer.query(`INSERT INTO "${organization.schema_name}".agent_tasks(id,dedupe_key,loop,title,status,requested_by,owner_id,amount,currency,simulated,outcome,resolved_via,level) VALUES($1,$1,'liquidity',$2,'done',$3,$3,-7200,'EUR',true,'Simulated 2 hires; €7,200 additional monthly payroll','forecast','AUTO')`, [taskId, title, owner]);
        await writer.query(`INSERT INTO "${organization.schema_name}".agent_task_steps(task_id,kind,source,text,evidence) VALUES($1,'market_research','external-public-research','Reviewed public job-market evidence',$2)`, [taskId, JSON.stringify({ ...research, label: 'Public hiring research' })]);
        await writer.query(`INSERT INTO "${organization.schema_name}".agent_task_steps(task_id,kind,source,text,detail) VALUES($1,'hiring_result','forecast','Completed the hiring simulation',$2)`, [taskId, JSON.stringify({ reply: report, hidden_internal_field: 'MUST-NOT-BE-EXPORTED' })]);
      } finally { await writer.end(); }
    }
    let caseIndex = 0;
    for (const theme of ['light', 'dark']) for (const viewport of [{ width: 1440, height: 1000 }, { width: 375, height: 812 }, { width: 812, height: 375 }]) {
      const taskId = `SC-${991 + caseIndex++}`;
      const page = await fixture.newPage({ viewport, reducedMotion: 'reduce' });
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(theme => localStorage.setItem('scout-theme', theme), theme);
      await page.goto(`${fixture.base}#chat`);
      await page.locator('#scout-chat-model').click();
      await page.locator('#scout-chat-configure-provider').click();
      await page.locator('#scout-external-key').fill('fixture-key-not-a-real-key');
      await page.locator('#scout-external-save').click();
      const outgoing = [];
      await page.route('**/api/chat', async route => {
        outgoing.push(route.request().postDataJSON());
        if (outgoing.length === 1) await saveTask(taskId); // Created after the dashboard's initial data load.
        await route.fulfill({ contentType: 'application/x-ndjson', body: [
          { type: 'step', text: 'Researching public job opportunities and salary estimates' },
          { type: 'research', research },
          { type: 'step', text: 'Granite created a hiring action in Liquidity' },
          { type: 'delta', text: report },
          { type: 'done', task_id: taskId, truncated: false },
        ].map(value => JSON.stringify(value)).join('\n') + '\n' });
      });
      await page.locator('#scout-chat-input').fill('Research cooks in Berlin and simulate hiring 2 at a recommended EUR salary from December for 6 months');
      await page.locator('#scout-chat-input').press('Enter');
      await page.getByRole('link', { name: `Open ${taskId}` }).waitFor();
      const source = page.locator('[id^="scout-chat-source-link-"]');
      assert.equal(await source.count(), 1); assert.equal(await source.getAttribute('target'), '_blank');
      assert.equal(await source.getAttribute('href'), research.sources[0].url);
      await page.locator('[id^="scout-chat-research-summary-"]').click();
      await page.getByText(research.summary, { exact: true }).waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.screenshot({ path: `${output}/research-${theme}-${viewport.width}.png` });
      await page.getByRole('link', { name: `Open ${taskId}` }).click();
      await page.locator(`#scout-hiring-report-${taskId}`).waitFor();
      assert.match(await page.locator(`#scout-hiring-report-${taskId}`).textContent(), /€43,200/);
      const detail = await page.request.get(`${new URL(fixture.base).origin}/api/tasks/${taskId}`, { headers: { 'X-Scout-Organization': fixture.slug } });
      assert.equal(detail.status(), 200); const detailText = await detail.text();
      assert.match(detailText, /hiringReport/); assert.doesNotMatch(detailText, /MUST-NOT-BE-EXPORTED/);
      await page.screenshot({ path: `${output}/liquidity-report-${theme}-${viewport.width}.png` });
      await page.evaluate(() => { location.hash = '#tasks?loop=liquidity'; });
      await page.locator(`[data-task-id="${taskId}"]`).waitFor();
      assert.match(await page.locator(`[data-task-id="${taskId}"]`).textContent(), /What if we hired 2 people/);
      await page.evaluate(() => { location.hash = '#chat'; });
      await page.locator('#scout-chat-input').fill('Forecast for 30 days');
      await page.locator('#scout-chat-input').press('Enter');
      await page.waitForFunction(() => document.querySelectorAll('#scout-chat-messages > li').length === 4);
      assert.equal(outgoing.length, 2); assert.equal(outgoing[1].messages.length, 1);
      assert.doesNotMatch(JSON.stringify(outgoing[1].messages), /private forecast|43,200|payroll/);
      assert.deepEqual(errors, []);
      await page.close(); console.log(`PASS hiring research → Liquidity report, privacy and layout: ${theme} ${viewport.width}`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
