import "server-only";
import { headers } from "next/headers";
import { Pool } from "pg";
import { authorized } from "./auth";
import { isDevelopmentPreview } from "./development-preview";
import { decimalCents, displayCents, financePeriod, type DashboardData, type Forecast } from "./finance-model";
import { jobFromDecision, parseJobLog, type DecisionJobRecord, type JobLogEntry } from "./agent-jobs";
export type { DashboardData, Employee, ExpenseCategory, FinancePeriod } from "./finance-model";

let pool: Pool | undefined;
function database() {
  if (pool) return pool;
  pool = new Pool({
    host: process.env.PGHOST, port: Number(process.env.PGPORT ?? 5432),
    database: process.env.PGDATABASE, user: process.env.PGUSER, password: process.env.PGPASSWORD,
    max: 5, connectionTimeoutMillis: 5000, idleTimeoutMillis: 30000,
    statement_timeout: 10000, options: "-c default_transaction_read_only=on",
  });
  pool.on("error", () => { /* Request errors are reported without connection details. */ });
  return pool;
}

async function forecast(): Promise<Forecast | null> {
  try {
    const response = await fetch(`${process.env.CFO_API_URL ?? "http://api:8000"}/v1/forecast/liquidity?horizon_days=90&granularity=day`, {
      cache: "no-store", signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) return null;
    const body = await response.json();
    if (body.stub !== false || !Array.isArray(body.points) || !body.points.length) return null;
    const start = Date.parse(body.as_of);
    const points = body.points.map((point: { date: string; balance: { amount: string } }) => ({
      day: (Date.parse(point.date) - start) / 86400000,
      balance: displayCents(decimalCents(point.balance.amount)),
    }));
    if (!Number.isFinite(start) || points.some((point: { day: number }) => !Number.isFinite(point.day))) return null;
    return { asOf: body.as_of, model: body.model.name, safetyBuffer: displayCents(decimalCents(body.safety_buffer.amount)), points };
  } catch {
    // Forecast availability must not hide the actual bank records.
    return null;
  }
}

export async function getJobLog(id: string): Promise<JobLogEntry[] | null> {
  if (!authorized((await headers()).get("authorization"))) throw new Error("Access denied");
  if (isDevelopmentPreview()) return (await import("./preview-data")).getPreviewJobLog(id);
  try {
    const result = await database().query("SELECT payload->'job_log' AS job_log FROM decisions WHERE id = $1", [id]);
    return result.rows.length ? parseJobLog(result.rows[0].job_log) : null;
  } catch {
    throw new Error("Job log is temporarily unavailable");
  }
}

export async function getDashboardData(): Promise<DashboardData> {
  // Enforce access at the data boundary too, including RSC and route requests.
  if (!authorized((await headers()).get("authorization"))) throw new Error("Access denied");
  if (isDevelopmentPreview()) return (await import("./preview-data")).getPreviewData();
  const client = await database().connect().catch(() => {
    throw new Error("Company data is temporarily unavailable");
  });
  let result: Omit<DashboardData, "forecast">;
  try {
    await client.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
    const summary = (await client.query(`
      SELECT to_char(max(booking_date), 'YYYY-MM-DD') AS as_of,
        coalesce((sum(amount) * 100)::bigint, 0)::text AS balance,
        count(*) FILTER (WHERE amount < 0 AND invoice_number IS NOT NULL AND NOT documented)::text AS undocumented
      FROM transactions`)).rows[0];
    if (!summary.as_of) throw new Error("No company transactions available");
    const periods = (await client.query(`
      SELECT CASE WHEN extract(month FROM booking_date) >= 10 THEN 'q4' ELSE 'earlier' END AS period,
        category, count(*)::text AS count,
        (coalesce(sum(amount) FILTER (WHERE amount > 0), 0) * 100)::bigint::text AS income,
        (coalesce(-sum(amount) FILTER (WHERE amount < 0), 0) * 100)::bigint::text AS expense
      FROM transactions WHERE date_trunc('year', booking_date) = date_trunc('year', $1::date)
      GROUP BY period, category ORDER BY category, period`, [summary.as_of])).rows;
    const grouped = new Map<string, { category: string | null; count: string; income: string; expense: string }>();
    for (const row of periods) {
      const key = row.category ?? "";
      const previous = grouped.get(key);
      grouped.set(key, previous ? { category: row.category,
        count: String(BigInt(previous.count) + BigInt(row.count)),
        income: String(BigInt(previous.income) + BigInt(row.income)),
        expense: String(BigInt(previous.expense) + BigInt(row.expense)) } : row);
    }
    const employees = (await client.query(`
      SELECT e.id, e.name, e.role, coalesce(t.name, 'Unassigned') AS team,
        p.salary FROM employees e LEFT JOIN teams t ON t.id = e.team_id
      LEFT JOIN LATERAL (
        SELECT (-sum(amount) * 100)::bigint::text AS salary FROM transactions
        WHERE category = 'payroll' AND lower(counterparty) = lower(e.name)
        GROUP BY date_trunc('month', booking_date) ORDER BY date_trunc('month', booking_date) DESC LIMIT 1
      ) p ON true ORDER BY e.name`)).rows.map(row => ({
        ...row, salary: row.salary === null ? null : displayCents(row.salary),
        initials: row.name.split(/\s+/).map((part: string) => part[0]).slice(0, 2).join(""),
        avatar: "avatar-green",
      }));
    const payroll = (await client.query(`
      SELECT category, (-sum(amount) * 100)::bigint::text AS amount FROM transactions
      WHERE category IN ('payroll', 'social_security')
        AND date_trunc('month', booking_date) = (SELECT date_trunc('month', max(booking_date)) FROM transactions WHERE category = 'payroll')
      GROUP BY category`)).rows;
    const pendingApprovals = Number((await client.query("SELECT count(*)::text AS count FROM approval_requests WHERE status = 'pending'")).rows[0].count);
    const records = (await client.query<DecisionJobRecord>(`
      SELECT d.id, to_char(d.ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS ts,
        d.summary, d.reason, d.level, d.status, d.executed_simulated, d.action_type, d.actor,
        a.approval, u.usage
      FROM (SELECT * FROM decisions ORDER BY ts DESC, id DESC LIMIT 50) d
      LEFT JOIN LATERAL (
        SELECT jsonb_build_object('id', id, 'status', status, 'approver', approver_id, 'question', question,
          'createdAt', to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
          'resolvedAt', to_char(resolved_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')) AS approval
        FROM approval_requests WHERE decision_id = d.id ORDER BY created_at DESC, id DESC LIMIT 1
      ) a ON true
      LEFT JOIN LATERAL (
        SELECT jsonb_build_object('calls', count(*), 'failedCalls', count(*) FILTER (WHERE NOT u.success),
          'latencyMs', coalesce(sum(u.latency_ms), 0),
          'tokens', coalesce(sum(u.prompt_tokens::bigint + u.completion_tokens::bigint), 0),
          'estimated', coalesce(bool_or(u.estimated), false),
          'events', coalesce(jsonb_agg(DISTINCT jsonb_build_object('id', u.event_id, 'kind', e.kind))
            FILTER (WHERE u.event_id IS NOT NULL), '[]'::jsonb)) AS usage
        FROM llm_usage u LEFT JOIN events e ON e.id = u.event_id WHERE u.decision_id = d.id
      ) u ON true
      ORDER BY d.ts DESC, d.id DESC`)).rows;
    const decisions = records.map(({ id, ts, summary, reason, level, status, executed_simulated }) =>
      ({ id, ts, summary, reason, level, status, executed_simulated }));
    await client.query("COMMIT");
    result = {
      asOf: summary.as_of, yearLabel: summary.as_of.slice(0, 4),
      year: financePeriod([...grouped.values()]), q4: financePeriod(periods.filter(row => row.period === "q4")),
      closingBalance: displayCents(summary.balance), employees,
      monthlyPayroll: displayCents(payroll.find(row => row.category === "payroll")?.amount ?? "0"),
      monthlyContributions: displayCents(payroll.find(row => row.category === "social_security")?.amount ?? "0"),
      undocumentedPayments: Number(summary.undocumented), pendingApprovals, decisions, jobs: records.map(jobFromDecision),
    };
  } catch {
    await client.query("ROLLBACK").catch(() => {});
    // Do not expose SQL, rows or connection strings in errors or logs.
    throw new Error("Company data is temporarily unavailable");
  } finally {
    client.release();
  }
  return { ...result, forecast: await forecast() };
}
