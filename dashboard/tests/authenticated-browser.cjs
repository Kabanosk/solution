// Local-only fixture for visual regressions. Never use application/production records.
const { randomUUID } = require('node:crypto');
const { Pool } = require('pg');
const assert = require('node:assert/strict');
exports.authenticatedFixture = async browser => {
  const origin = process.env.BASE_URL || 'http://127.0.0.1:3017';
  const url = process.env.SCOUT_AUTH_TEST_DATABASE_URL;
  assert.ok(url && ['localhost', '127.0.0.1'].includes(new URL(url).hostname) && ['localhost', '127.0.0.1'].includes(new URL(origin).hostname), 'An isolated local database/server is required');
  const slug = `visual-${randomUUID().slice(0, 8)}`;
  const context = await browser.newContext();
  let state;
  try {
    const response = await context.request.post(`${origin}/api/auth/register`, { headers: { Origin: origin }, data: { name: 'Visual Test Studio', slug, email: `${slug}@example.test`, ownerName: 'Visual Test Owner', password: randomUUID() } });
    assert.equal(response.status(), 200, `Local fixture signup failed: ${await response.text()}`);
    state = await context.storageState();
  } finally { await context.close(); }
  const pool = new Pool({ connectionString: url });
  try {
    const organization = (await pool.query('SELECT schema_name FROM scout_auth.organizations WHERE slug=$1', [slug])).rows[0];
    assert.ok(organization, 'Signup and SCOUT_AUTH_TEST_DATABASE_URL must use the same isolated database');
    // These visual fixtures represent an existing workspace, not the signup/onboarding acceptance case.
    await pool.query("UPDATE scout_auth.workspace_setup SET status='dismissed' WHERE organization_id=(SELECT id FROM scout_auth.organizations WHERE slug=$1)", [slug]);
    const schema = organization.schema_name;
    assert.match(schema, /^tenant_[a-f0-9]{32}$/);
    for (const [id, kind] of [['bank','bank'],['invoicing','invoicing'],['mail','mail'],['slack','chat'],['laptops','files'],['ksef','invoicing']]) {
      const config = id === 'slack' ? { scopes: ['channels:join'], missing_scopes: [] } : id === 'ksef' ? { phase: 'done', received: 1, invoices: [{ number: 'VISUAL-1', total: '10.00', currency: 'EUR' }] } : {};
      await pool.query(`INSERT INTO "${schema}".connections(id,kind,provider,display_name,status,config) VALUES($1,$2,'test-fixture',$1,$3,$4)`, [id,kind,id === 'mail' ? 'disconnected' : 'connected',JSON.stringify(config)]);
    }
    await pool.query(`INSERT INTO "${schema}".llm_usage(ts,model,prompt_tokens,completion_tokens,latency_ms) VALUES(now(),'visual-test-model',100,20,300)`);
  } finally { await pool.end(); }
  return { base: `${origin}/${slug}`, slug, newPage: options => browser.newPage({ ...options, storageState: state }) };
};
