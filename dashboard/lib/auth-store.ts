import { randomUUID } from "node:crypto";
import type { Pool, PoolClient } from "pg";
import { newToken, passwordHash, tokenHash, validSlug, verifyPassword, registeredSchema } from "./auth-crypto.ts";

export type Session = { userId: string; organizationId: string; slug: string; organizationName: string; schema: string; name: string; email: string; employeeId: string; role: string; legacy: boolean };
export type OrganizationDraft = { name: string; slug: string };
type Signup = OrganizationDraft & { email: string; ownerName: string; password: string };
type Proof = { kind: "password"; password: string } | { kind: "google"; subject: string };
type Queryable = Pick<Pool, "query"> | Pick<PoolClient, "query">;
const SESSION_COLUMNS = `u.id AS "userId",o.id AS "organizationId",o.slug,o.name AS "organizationName",o.schema_name AS schema,u.name,u.email,m.employee_id AS "employeeId",m.role,o.legacy`;
const JOINS = `JOIN scout_auth.users u ON u.id=m.user_id JOIN scout_auth.organizations o ON o.id=m.organization_id`;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export class AuthStore {
  private pool: Pool;
  constructor(pool: Pool) { this.pool = pool; }

  async organization(slug: string) {
    if (!validSlug(slug)) return null;
    const row = (await this.pool.query("SELECT slug,name FROM scout_auth.organizations WHERE slug=$1", [slug])).rows[0];
    return row ? { slug: row.slug as string, name: row.name as string } : null;
  }

  async findOrganization(value: string) {
    const query = value.trim();
    if (!query || query.length > 100) return null;
    const exact = await this.organization(query);
    if (exact) return exact;
    const rows = (await this.pool.query("SELECT slug,name FROM scout_auth.organizations WHERE lower(name)=lower($1) LIMIT 2", [query])).rows;
    if (rows.length > 1) throw new Error("More than one organization uses this name. Enter the organization URL.");
    return rows.length === 1 ? { slug: rows[0].slug as string, name: rows[0].name as string } : null;
  }

  async register(input: Signup) {
    return this.createOrganization(input, { kind: "password", password: input.password });
  }

  // Only called with the subject/email from a cryptographically verified Google callback.
  async registerGoogle(input: OrganizationDraft, identity: { subject: string; email: string; name: string }) {
    if (!identity.subject || identity.subject.length > 255) throw new Error("Invalid Google identity.");
    return this.createOrganization({ ...input, email: identity.email, ownerName: identity.name }, { kind: "google", subject: identity.subject });
  }

  private async createOrganization(input: Omit<Signup, "password">, proof: Proof) {
    const name = input.name.trim(), owner = input.ownerName.trim(), email = input.email.trim().toLowerCase();
    if (!validSlug(input.slug) || !name || name.length > 100 || !owner || owner.length > 100 || !EMAIL.test(email) || email.length > 254) throw new Error("Check your organization name, URL, name and email.");
    const hash = proof.kind === "password" ? await passwordHash(proof.password) : null;
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      const existing = (await client.query("SELECT id,password_hash,google_subject FROM scout_auth.users WHERE email=$1 FOR UPDATE", [email])).rows[0];
      if (existing && proof.kind === "google" && existing.google_subject !== proof.subject) throw new Error("Sign in to your existing account and link Google first.");
      if (existing && proof.kind === "password" && !(await verifyPassword(proof.password, existing.password_hash ?? ""))) throw new Error("Use your existing account password or a different email.");
      if (existing) {
        const owned = (await client.query("SELECT count(*)::int AS n FROM scout_auth.memberships WHERE user_id=$1 AND role='owner'", [existing.id])).rows[0].n;
        if (owned >= 3) throw new Error("Each account can own up to three organizations.");
      }
      const userId = existing?.id ?? randomUUID(), orgId = randomUUID();
      const schema = `tenant_${orgId.replaceAll("-", "")}`;
      if (!existing) await client.query("INSERT INTO scout_auth.users(id,email,name,password_hash,google_subject) VALUES($1,$2,$3,$4,$5)", [userId, email, owner, hash, proof.kind === "google" ? proof.subject : null]);
      await client.query("INSERT INTO scout_auth.organizations(id,slug,name,schema_name,legacy) VALUES($1,$2,$3,$4,false)", [orgId, input.slug, name, schema]);
      await client.query("SELECT scout_auth.provision_organization($1::uuid)", [orgId]);
      // Only new workspaces start onboarding; column defaults (pending, revision 0) come from the schema.
      await client.query("INSERT INTO scout_auth.workspace_setup(organization_id) VALUES($1)", [orgId]);
      // schema is derived only from a server-generated UUID, never from a requested slug.
      await client.query(`INSERT INTO "${schema}".employees(id,name,role) VALUES($1,$2,'CEO')`, [userId, owner]);
      await client.query("INSERT INTO scout_auth.memberships(organization_id,user_id,role,employee_id) VALUES($1,$2,'owner',$3)", [orgId, userId, userId]);
      const result = await this.issue(client, userId, input.slug);
      await client.query("COMMIT");
      return result!;
    } catch (error) {
      await client.query("ROLLBACK");
      if ((error as { code?: string }).code === "23505") throw new Error("That organization URL or account is already registered.");
      throw error;
    } finally { client.release(); }
  }

  private async issue(db: Queryable, userId: string, slug: string) {
    const session = (await db.query(`SELECT ${SESSION_COLUMNS} FROM scout_auth.memberships m ${JOINS} WHERE m.user_id=$1 AND o.slug=$2`, [userId, slug])).rows[0] as Session | undefined;
    if (!session || !registeredSchema(session.organizationId, session.schema, session.legacy)) return null;
    const token = newToken();
    await db.query("INSERT INTO scout_auth.sessions(token_hash,user_id,organization_id,expires_at) VALUES($1,$2,$3,now()+interval '12 hours')", [tokenHash(token), userId, session.organizationId]);
    return { token, session };
  }

  async login(slug: string, email: string, password: string) {
    const row = (await this.pool.query("SELECT id,password_hash FROM scout_auth.users WHERE email=$1", [email.trim().toLowerCase()])).rows[0];
    // Also run the expensive KDF on unknown accounts, avoiding a cheap account-enumeration oracle.
    const dummy = "scrypt$00000000000000000000000000000000$" + "00".repeat(64);
    const correct = await verifyPassword(password, row?.password_hash ?? dummy);
    return row && correct ? this.issue(this.pool, row.id, slug) : null;
  }

  async session(token: string | undefined, slug: string | undefined): Promise<Session | null> {
    if (!token || token.length > 128 || !slug || !validSlug(slug)) return null;
    const result = (await this.pool.query(`SELECT ${SESSION_COLUMNS} FROM scout_auth.sessions s JOIN scout_auth.memberships m ON m.organization_id=s.organization_id AND m.user_id=s.user_id ${JOINS} WHERE s.token_hash=$1 AND s.expires_at>now() AND o.slug=$2`, [tokenHash(token), slug])).rows[0] as Session | undefined;
    return result && registeredSchema(result.organizationId, result.schema, result.legacy) ? result : null;
  }

  async logout(token: string) { await this.pool.query("DELETE FROM scout_auth.sessions WHERE token_hash=$1", [tokenHash(token)]); }

  async googleLogin(slug: string, subject: string, email: string) {
    const row = (await this.pool.query("SELECT id FROM scout_auth.users WHERE google_subject=$1 AND email=$2", [subject, email.toLowerCase()])).rows[0];
    return row ? this.issue(this.pool, row.id, slug) : null;
  }

  async linkGoogle(userId: string, subject: string, email: string) {
    // Linking is only called after an authenticated, browser-bound explicit link flow.
    const result = await this.pool.query("UPDATE scout_auth.users SET google_subject=$2 WHERE id=$1 AND email=$3 AND (google_subject IS NULL OR google_subject=$2) RETURNING id", [userId, subject, email.toLowerCase()]);
    if (!result.rowCount) throw new Error("Google identity does not match this account.");
  }

  async startOAuth(slug: string, browser: string, linkUserId?: string) {
    const org = (await this.pool.query("SELECT id FROM scout_auth.organizations WHERE slug=$1", [slug])).rows[0];
    if (!org) throw new Error("Organization not found.");
    const state = newToken(), verifier = newToken(), nonce = newToken();
    await this.pool.query("INSERT INTO scout_auth.oauth_transactions(state_hash,browser_hash,organization_id,verifier,nonce,expires_at,link_user_id) VALUES($1,$2,$3,$4,$5,now()+interval '10 minutes',$6)", [tokenHash(state), tokenHash(browser), org.id, verifier, nonce, linkUserId ?? null]);
    return { state, verifier, nonce };
  }

  async startGoogleRegistration(input: OrganizationDraft, browser: string) {
    const registration = { name: input.name.trim(), slug: input.slug };
    if (!registration.name || registration.name.length > 100 || !validSlug(registration.slug)) throw new Error("Check your organization name and URL.");
    if (await this.organization(registration.slug)) throw new Error("That organization URL is already registered.");
    const state = newToken(), verifier = newToken(), nonce = newToken();
    await this.pool.query("INSERT INTO scout_auth.oauth_transactions(state_hash,browser_hash,verifier,nonce,expires_at,registration) VALUES($1,$2,$3,$4,now()+interval '10 minutes',$5)", [tokenHash(state), tokenHash(browser), verifier, nonce, JSON.stringify(registration)]);
    return { state, verifier, nonce };
  }

  async consumeOAuth(state: string, browser: string) {
    if (!state || !browser || state.length > 128 || browser.length > 128) return null;
    const row = (await this.pool.query(`WITH consumed AS (DELETE FROM scout_auth.oauth_transactions WHERE state_hash=$1 AND browser_hash=$2 AND expires_at>now() RETURNING organization_id,verifier,nonce,link_user_id,registration) SELECT COALESCE(o.slug,'') AS slug,t.verifier,t.nonce,t.link_user_id AS "linkUserId",t.registration FROM consumed t LEFT JOIN scout_auth.organizations o ON o.id=t.organization_id`, [tokenHash(state), tokenHash(browser)])).rows[0];
    return row as { slug: string; verifier: string; nonce: string; linkUserId: string | null; registration: OrganizationDraft | null } | undefined ?? null;
  }

  async rateLimit(key: string, max = 12) {
    await this.pool.query("DELETE FROM scout_auth.rate_limits WHERE reset_at<now()");
    await this.pool.query("DELETE FROM scout_auth.sessions WHERE expires_at<now()");
    await this.pool.query("DELETE FROM scout_auth.oauth_transactions WHERE expires_at<now()");
    const row = (await this.pool.query(`INSERT INTO scout_auth.rate_limits(key,attempts,reset_at) VALUES($1,1,now()+interval '15 minutes') ON CONFLICT(key) DO UPDATE SET attempts=CASE WHEN scout_auth.rate_limits.reset_at<now() THEN 1 ELSE scout_auth.rate_limits.attempts+1 END,reset_at=CASE WHEN scout_auth.rate_limits.reset_at<now() THEN now()+interval '15 minutes' ELSE scout_auth.rate_limits.reset_at END RETURNING attempts`, [tokenHash(key)])).rows[0];
    return row.attempts <= max;
  }
}
