// Run against a local dashboard server; no database or Google credentials are required.
const assert = require('node:assert/strict');
const base = process.env.BASE_URL || 'http://127.0.0.1:3019';
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw new Error('Use a local test server, not production');

(async () => {
  for (const [path, heading] of [['/privacy', 'Privacy Policy'], ['/terms', 'Terms of Service']]) {
    const response = await fetch(base + path, { redirect: 'manual' });
    assert.equal(response.status, 200, `${path} must be publicly accessible without a session`);
    assert.match(response.headers.get('content-type') || '', /text\/html/);
    const html = await response.text();
    assert.ok(html.includes(heading), `${path} must render its own legal document`);
    assert.doesNotMatch(response.headers.get('x-robots-tag') || '', /noindex/i, `${path} must be reviewable by Google`);
    assert.ok(html.includes('Piotr Warzachowski'), `${path} must name the operator`);
    assert.ok(html.includes('warzachowskip@gmail.com'), `${path} must provide the confirmed contact`);
    assert.ok(html.includes('2026'), `${path} must show the publication year`);
  }
  console.log('PASS public legal pages without authentication');
})().catch(error => { console.error(error); process.exitCode = 1; });
