// Callback GETs have no tenant header; route-level state/session validation must handle them.
const { request } = require('playwright');
const assert = require('node:assert/strict');
const base = process.env.BASE_URL || 'http://127.0.0.1:3137';
if (!['localhost', '127.0.0.1'].includes(new URL(base).hostname)) throw new Error('Use a local verification server');
(async () => {
  const context = await request.newContext();
  try {
    for (const provider of ['google', 'slack']) {
      const response = await context.get(`${base}/api/onboarding/callback/${provider}?state=invalid&code=invalid`, { maxRedirects: 0 });
      assert.equal(response.status(), 303, `${provider}: GET reaches callback's own missing-proof handling, not middleware's missing-header 401`);
      const destination = new URL(response.headers().location, base);
      assert.equal(destination.origin, base);
      assert.equal(destination.searchParams.has('code'), false);
      assert.equal(destination.searchParams.has('state'), false);
      const post = await context.post(`${base}/api/onboarding/callback/${provider}`, { headers: { Origin: base }, data: {} });
      assert.equal(post.status(), 401, 'The narrow callback exemption cannot expose anonymous writes');
    }
    assert.equal((await context.get(`${base}/api/onboarding/callback/unknown`, { maxRedirects: 0 })).status(), 401);
    assert.equal((await context.get(`${base}/api/onboarding`, { maxRedirects: 0 })).status(), 401);
    console.log('PASS callback GET routing with invalid state, no token forwarding, and anonymous write/unknown-provider rejection');
  } finally { await context.dispose(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
