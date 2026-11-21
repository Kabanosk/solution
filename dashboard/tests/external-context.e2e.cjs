// Local authenticated UI regression. Provider responses and opaque context tokens are fixtures.
const { chromium } = require('playwright');
const { authenticatedFixture } = require('./authenticated-browser.cjs');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const output = process.env.SCREENSHOT_DIR || '/tmp/scout-external-context-screens';
const fixtureKey = 'fixture-api-key-not-a-real-key';
const initialQuestion = 'Chcę zatrudnić nowego pizzaiolo z co najmniej 2 latami doświadczenia do pizzerii na Bałutach w Łodzi. Z jakimi kosztami to się wiąże?';
const firstReply = 'Mam publiczne informacje o kosztach zatrudnienia pizzaiolo na Bałutach z minimum 2 latami doświadczenia. Czy chcesz oszacować koszt wybranego wynagrodzenia i okresu zatrudnienia?';
const followupReply = 'Tak — dla pizzaiolo na Bałutach z minimum 2 latami doświadczenia ustalmy miesięczne wynagrodzenie, miesiąc rozpoczęcia i długość symulacji.';
const privateReply = 'PRYWATNY-WYNIK-GRANITE: prognozowane saldo firmy wynosi €17,654.32. Tego wyniku nie udostępniamy zewnętrznemu modelowi.';
const afterPrivateReply = 'Prywatny wynik pozostaje w Granite. Mogę pomóc z publicznymi założeniami zatrudnienia pizzaiolo na Bałutach.';
const research = {
  summary: 'Przykład testowy publicznych informacji o pizzaiolo na Bałutach: co najmniej 2 lata doświadczenia. To dane testowe, nie rzeczywista wycena wynagrodzenia.',
  sources: [{ title: 'Publiczne statystyki rynku pracy', url: 'https://stat.gov.pl/' }],
  researched_at: '2026-11-21T12:00:00Z',
};
const tokens = Array.from({ length: 10 }, (_, index) => `opaque-fixture-context-${index + 1}-not-private-results`);

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH });
  try {
    const fixture = await authenticatedFixture(browser);
    for (const theme of ['light', 'dark']) for (const viewport of [{ width: 1440, height: 1000 }, { width: 375, height: 812 }]) {
      const page = await fixture.newPage({ viewport, reducedMotion: 'reduce' });
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(value => localStorage.setItem('scout-theme', value), theme);
      await page.goto(`${fixture.base}#chat`);
      async function configure(provider = 'openai') {
        await page.locator('#scout-chat-model').click();
        await page.locator('#scout-chat-configure-provider').click();
        await page.locator('#scout-external-provider').selectOption(provider);
        await page.locator('#scout-external-key').fill(fixtureKey);
        await page.locator('#scout-external-save').click();
        await page.getByRole('dialog').waitFor({ state: 'hidden' });
      }
      await configure();
      const outgoing = [];
      await page.route('**/api/chat', async route => {
        const request = route.request().postDataJSON();
        outgoing.push(request);
        assert.equal(request.model, 'external');
        assert.equal(request.messages.length, 1, 'Only the latest user message is serialized in the browser');
        assert.equal(request.messages[0].role, 'user');
        assert.doesNotMatch(JSON.stringify(request), /PRYWATNY-WYNIK-GRANITE|17,654\.32/);
        const index = outgoing.length - 1;
        const expectedContext = index > 0 && index < 4 ? tokens[index - 1] : undefined;
        assert.equal(request.externalContext, expectedContext, 'Follow-ups use opaque context; reset actions clear it');
        const text = [firstReply, request.externalContext === tokens[0] ? followupReply : 'Cześć! Jak mogę Ci pomóc?', privateReply, afterPrivateReply,
          'Nowa rozmowa bez poprzedniego kontekstu.', 'Nowa rozmowa po zmianie modelu.', 'Nowa rozmowa po zmianie dostawcy.',
          'Nowa rozmowa po ponownej konfiguracji.', 'Nowa rozmowa po odświeżeniu.'][index];
        assert.ok(text, 'Unexpected chat request');
        const events = [
          ...(index === 0 ? [{ type: 'research', research }] : []),
          { type: 'external_context', token: tokens[index] },
          { type: 'delta', text },
          { type: 'done', truncated: false, ...(index === 2 ? { task_id: 'SC-777' } : {}) },
        ];
        await route.fulfill({ contentType: 'application/x-ndjson', body: events.map(event => JSON.stringify(event)).join('\n') + '\n' });
      });
      async function send(text, expected) {
        await page.locator('#scout-chat-input').fill(text);
        await page.locator('#scout-chat-input').press('Enter');
        await page.getByText(expected, { exact: true }).waitFor();
        await page.locator('#scout-chat-send').waitFor();
      }
      await send(initialQuestion, firstReply);
      await send('tak', followupReply);
      assert.equal(outgoing[0].conversationId, outgoing[1].conversationId);
      assert.deepEqual(outgoing[1].messages, [{ role: 'user', content: 'tak' }]);
      await page.screenshot({ path: `${output}/followup-${theme}-${viewport.width}.png` });
      await send('Symuluj zatrudnienie jednej osoby od grudnia na 6 miesięcy.', privateReply);
      // The completed local action triggers router.refresh(); client tab state must survive it.
      await send('Co dalej?', afterPrivateReply);
      assert.equal(outgoing[2].conversationId, outgoing[3].conversationId);
      assert.equal(outgoing[3].externalContext, tokens[2]);
      assert.deepEqual(outgoing[3].messages, [{ role: 'user', content: 'Co dalej?' }]);
      assert.equal(await page.evaluate(values => values.some(value => JSON.stringify(localStorage).includes(value) || JSON.stringify(sessionStorage).includes(value)), [...tokens, fixtureKey]), false);

      await page.locator('#scout-chat-new').click();
      await send('Nowe pytanie.', 'Nowa rozmowa bez poprzedniego kontekstu.');
      assert.notEqual(outgoing[3].conversationId, outgoing[4].conversationId);
      await page.locator('#scout-chat-model').click();
      await page.locator('#scout-chat-model-modelcar-granite-4-0-h-tiny').click();
      await page.locator('#scout-chat-model').click();
      await page.locator('#scout-chat-model-external').click();
      await send('Po przełączeniu modelu.', 'Nowa rozmowa po zmianie modelu.');
      assert.notEqual(outgoing[4].conversationId, outgoing[5].conversationId);
      await configure('mistral');
      await send('Po zmianie dostawcy.', 'Nowa rozmowa po zmianie dostawcy.');
      assert.equal(outgoing[6].external.provider, 'mistral');
      assert.notEqual(outgoing[5].conversationId, outgoing[6].conversationId);
      await page.locator('#scout-chat-model').click();
      await page.locator('#scout-chat-configure-provider').click();
      await page.locator('#scout-external-remove').click();
      await configure();
      await send('Ponowna konfiguracja.', 'Nowa rozmowa po ponownej konfiguracji.');
      assert.notEqual(outgoing[6].conversationId, outgoing[7].conversationId);
      await page.reload();
      await configure();
      await send('Po odświeżeniu.', 'Nowa rozmowa po odświeżeniu.');
      assert.notEqual(outgoing[7].conversationId, outgoing[8].conversationId);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.deepEqual(errors, []);
      await page.close();
      console.log(`PASS public follow-up context, private result isolation, tab-memory lifecycle: ${theme} ${viewport.width}`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
