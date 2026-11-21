import { registeredSchema } from "./auth-crypto.ts";
import { GUARDRAILS_DEFAULT, guardrailsAudit, type GuardrailsSettings } from "./guardrails-model.ts";

// The slice of a pg Pool this needs (a Pool fits; the tests pass a recording stand-in).
type Query = (text: string, values?: unknown[]) => Promise<{ rows: any[] }>; // eslint-disable-line @typescript-eslint/no-explicit-any
export type GuardrailsDb = { query: Query; connect(): Promise<{ query: Query; release(): void }> };
type Owner = { organizationId: string; schema: string; legacy: boolean; employeeId: string; email: string; name: string };

export async function readGuardrails(db: Pick<GuardrailsDb, "query">, organizationId: string): Promise<GuardrailsSettings> {
  const row = (await db.query("SELECT enabled,updated_by,updated_at FROM scout_auth.guardrails_settings WHERE organization_id=$1", [organizationId])).rows[0];
  if (!row) return GUARDRAILS_DEFAULT;
  return { enabled: row.enabled !== false, updatedBy: row.updated_by ?? null, updatedAt: row.updated_at instanceof Date ? row.updated_at.toISOString() : null };
}

/** Set the switch; when it changes, one `guardrails_toggled` row goes to the workspace's own audit_log in the same
 * transaction. The worker and the api pick the new value up within their 5 s cache. */
export async function saveGuardrails(db: GuardrailsDb, owner: Owner, enabled: boolean): Promise<GuardrailsSettings> {
  if (!registeredSchema(owner.organizationId, owner.schema, owner.legacy)) throw new Error("Invalid organization.");
  const who = owner.employeeId || owner.email; // the audit requester, like every other audit row
  const by = owner.name.trim() || owner.email; // shown on the Audit page next to the switch
  const client = await db.connect();
  try {
    await client.query("BEGIN");
    await client.query("INSERT INTO scout_auth.guardrails_settings(organization_id) VALUES($1) ON CONFLICT DO NOTHING", [owner.organizationId]);
    const before = (await client.query("SELECT enabled FROM scout_auth.guardrails_settings WHERE organization_id=$1 FOR UPDATE", [owner.organizationId])).rows[0];
    if (before?.enabled !== enabled) {
      await client.query("UPDATE scout_auth.guardrails_settings SET enabled=$2,updated_by=$3,updated_at=now() WHERE organization_id=$1", [owner.organizationId, enabled, by]);
      await client.query("SELECT set_config('search_path',$1,true)", [owner.schema]); // local: ends with this transaction
      const row = guardrailsAudit(enabled, who);
      await client.query("INSERT INTO audit_log(actor,requester,role,action,target,resource,decision,policy,reason) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        [row.actor, row.requester, row.role, row.action, row.target, row.resource, row.decision, row.policy, row.reason]);
    }
    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK").catch(() => undefined);
    throw error;
  } finally { client.release(); }
  return readGuardrails(db, owner.organizationId);
}
