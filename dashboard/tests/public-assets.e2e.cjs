// Read-only probes: auth must not turn public font files into organization redirects.
const assert = require('node:assert/strict');
const base = process.env.BASE_URL || 'http://127.0.0.1:3017';
(async () => {
  for (const file of ['Inter.woff2', 'Mono.woff2']) {
    const response = await fetch(`${base}/fonts/${file}`, { redirect: 'manual' });
    assert.equal(response.status, 200, `${file} must be public, not treated as an organization URL`);
    assert.match(response.headers.get('content-type'), /font\/woff2/);
    assert.equal(Buffer.from(await response.arrayBuffer()).subarray(0, 4).toString(), 'wOF2');
    assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
    assert.equal(response.headers.get('x-frame-options'), 'DENY');
  }
  for (const extra of [{}, { Authorization: 'Basic dGVzdDp0ZXN0' }]) {
    const denied = await fetch(`${base}/api/dashboard`, { headers: { 'X-Scout-Organization': 'pizzeria-amalfi', ...extra }, redirect: 'manual' });
    assert.equal(denied.status, 401, 'Public fonts must not make company data public');
  }
  console.log('Font bytes, MIME, security headers and anonymous/Basic-only API denials: PASS');
  if (['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) {
    const { chromium } = require('playwright');
    const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH });
    try {
      for (const width of [1440, 375]) for (const theme of ['light', 'dark']) {
        const page = await browser.newPage({ viewport: { width, height: 1000 } });
        await page.addInitScript(value => localStorage.setItem('scout-theme', value), theme);
        await page.goto(base);
        await page.locator('#scout-auth-heading').waitFor();
        const fonts = await page.evaluate(async () => {
          const loaded = await Promise.all(['Inter', 'Geist Mono'].map(family => document.fonts.load(`400 16px "${family}"`)));
          await document.fonts.ready;
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          return loaded.map(faces => faces.map(face => ({ family: face.family, status: face.status })));
        });
        for (const faces of fonts) { assert.ok(faces.length > 0); assert.ok(faces.every(face => face.status === 'loaded')); }
        const session = await page.context().newCDPSession(page);
        await session.send('DOM.enable'); await session.send('CSS.enable');
        const { root } = await session.send('DOM.getDocument');
        // Ask for actual visible heading glyphs, not the body's unrelated descendant styles.
        const { nodeId } = await session.send('DOM.querySelector', { nodeId: root.nodeId, selector: '#scout-auth-heading' });
        const rendered = await session.send('CSS.getPlatformFontsForNode', { nodeId });
        assert.ok(rendered.fonts.some(font => font.isCustomFont && font.familyName.includes('Inter')), 'Heading glyphs must use the downloaded Inter webfont');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        if (process.env.SCREENSHOT_DIR) {
          require('node:fs').mkdirSync(process.env.SCREENSHOT_DIR, { recursive: true });
          await page.screenshot({ path: require('node:path').join(process.env.SCREENSHOT_DIR, `font-${theme}-${width}.png`) });
        }
        await page.close();
      }
      console.log('Loaded Inter/Geist Mono, actual Inter webfont glyphs, light/dark desktop/mobile: PASS');
    } finally { await browser.close(); }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
