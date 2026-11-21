import { randomUUID } from "node:crypto";
import type { Pool, PoolClient } from "pg";
import { newToken, passwordHash, tokenHash, validSlug, verifyPassword, registeredSchema } from "./auth-crypto.ts";

export type Session = { userId: string; organizationId: string; slug: string; organizationName: string; schema: string; name: string; email: string; employeeId: string; role: string; legacy: boolean };
export type OrganizationDraft = { name: string; slug: string };
type Join = { slug: string; token: string; firstName: string; lastName: string; email: string; password: string };
export type Invite = { organization: { slug: string; name: string }; slackName: string | null };
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

  /** A Slack join link: the organization it belongs to and the member's Slack name; null when unusable. */
  async invite(slug: string, token: string): Promise<Invite | null> {
    const org = await this.inviteOrganization(this.pool, slug);
    if (!org || !token || token.length > 128) return null;
    const row = (await this.pool.query(`SELECT slack_name FROM "${org.schema}".slack_members WHERE invite_token_hash=$1 AND status='invited' AND invited_at>now()-interval '30 days'`, [tokenHash(token)]).catch(() => ({ rows: [] }))).rows[0];
    return row ? { organization: { slug: org.slug, name: org.name }, slackName: row.slack_name ?? null } : null;
  }

  private async inviteOrganization(db: Queryable, slug: string) {
    if (!validSlug(slug)) return null;
    const org = (await db.query("SELECT id,slug,name,schema_name AS schema,legacy FROM scout_auth.organizations WHERE slug=$1", [slug])).rows[0];
    // The schema name is only ever interpolated after this registry check.
    return org && registeredSchema(org.id, org.schema, org.legacy) ? org as { id: string; slug: string; name: string; schema: string; legacy: boolean } : null;
  }

  /** Accept a Slack join link: a login (new, or the existing one for that email), an employee profile linked to the
   *  Slack user, and membership in the organization. One account can belong to many organizations. */
  async join(input: Join) {
    const first = input.firstName.trim(), last = input.lastName.trim(), email = input.email.trim().toLowerCase();
    const name = `${first} ${last}`;
    if (!first || !last || name.length > 100 || !EMAIL.test(email) || email.length > 254) throw new Error("Enter your first name, last name and a valid email.");
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      const org = await this.inviteOrganization(client, input.slug);
      const invite = org && input.token.length <= 128 ? (await client.query(`SELECT slack_user_id FROM "${org.schema}".slack_members WHERE invite_token_hash=$1 AND status='invited' AND invited_at>now()-interval '30 days' FOR UPDATE`, [tokenHash(input.token)])).rows[0] : undefined;
      if (!org || !invite) throw new Error("This invite link is invalid or was already used. Message Scout on Slack for a new one.");
      const existing = (await client.query("SELECT id,password_hash FROM scout_auth.users WHERE email=$1 FOR UPDATE", [email])).rows[0];
      if (existing && !(await verifyPassword(input.password, existing.password_hash ?? ""))) throw new Error("Use your existing account password or a different email.");
      const userId = existing?.id ?? randomUUID();
      if (!existing) await client.query("INSERT INTO scout_auth.users(id,email,name,password_hash) VALUES($1,$2,$3,$4)", [userId, email, name, await passwordHash(input.password)]);
      const member = (await client.query("SELECT employee_id FROM scout_auth.memberships WHERE organization_id=$1 AND user_id=$2", [org.id, userId])).rows[0];
      const linked = (await client.query(`SELECT id FROM "${org.schema}".employees WHERE slack_user_id=$1 ORDER BY id LIMIT 1`, [invite.slack_user_id])).rows[0];
      let employeeId: string = member?.employee_id ?? linked?.id;
      if (employeeId) {
        await client.query(`UPDATE "${org.schema}".employees SET name=$2, slack_user_id=COALESCE(slack_user_id,$3), slack_name=COALESCE(slack_name,$2) WHERE id=$1`, [employeeId, name, invite.slack_user_id]);
      } else {
        const base = name.toLowerCase().replaceAll("ł", "l").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]+/g, ".").replace(/^\.|\.$/g, "") || `slack.${invite.slack_user_id.toLowerCase()}`;
        employeeId = base;
        for (let n = 2; (await client.query(`SELECT 1 FROM "${org.schema}".employees WHERE id=$1`, [employeeId])).rowCount; n++) employeeId = `${base}.${n}`;
        await client.query(`INSERT INTO "${org.schema}".employees(id,name,role,slack_user_id,slack_name) VALUES($1,$2,'Staff',$3,$2)`, [employeeId, name, invite.slack_user_id]);
      }
      if (!member) await client.query("INSERT INTO scout_auth.memberships(organization_id,user_id,role,employee_id) VALUES($1,$2,'member',$3)", [org.id, userId, employeeId]);
      await client.query(`UPDATE "${org.schema}".slack_members SET status='registered', employee_id=$2, registered_at=now(), invite_token_hash=NULL WHERE slack_user_id=$1`, [invite.slack_user_id, employeeId]);
      const result = await this.issue(client, userId, org.slug);
      await client.query("COMMIT");
      return result!;
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally { client.release(); }
  }

  /** Every organization this account belongs to (the workspace switcher). */
  async organizations(userId: string) {
    return (await this.pool.query("SELECT o.slug,o.name FROM scout_auth.memberships m JOIN scout_auth.organizations o ON o.id=m.organization_id WHERE m.user_id=$1 ORDER BY lower(o.name)", [userId])).rows as { slug: string; name: string }[];
  }

  /** Move a signed-in account to another organization it is a member of: a new session for that one. */
  async switchOrganization(token: string | undefined, slug: string) {
    if (!token || token.length > 128 || !validSlug(slug)) return null;
    const row = (await this.pool.query("SELECT user_id FROM scout_auth.sessions WHERE token_hash=$1 AND expires_at>now()", [tokenHash(token)])).rows[0];
    return row ? this.issue(this.pool, row.user_id, slug) : null;
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
