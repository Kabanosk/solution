import "server-only";
import { Pool, type PoolClient } from "pg";
import { authStore, requireSession } from "./auth";
import { apiFetch } from "./scout-api";
import { decimalCents, displayCents, financePeriod, quarterSpend, type DashboardData, type Forecast } from "./finance-model";
import { loadRecords, loadScout, loadTaskDetail } from "./scout";
import type { RecordKind, ScoutRecords, TaskDetail } from "./scout-model";
export type { DashboardData, Employee, ExpenseCategory, FinancePeriod } from "./finance-model";

let pool: Pool | undefined;
function database() {
  if (pool) return pool;
  pool = new Pool({
    connectionString: process.env.DATABASE_URL,
    host: process.env.PGHOST, port: Number(process.env.PGPORT ?? 5432),
    database: process.env.PGDATABASE, user: process.env.PGUSER, password: process.env.PGPASSWORD,
    max: 5, connectionTimeoutMillis: 5000, idleTimeoutMillis: 30000,
    statement_timeout: 10000, options: "-c default_transaction_read_only=on",
  });
  pool.on("error", () => { /* Request errors are reported without connection details. */ });
  return pool;
}

// Forecast responses are request-local. Never share a tenant-trained forecast across sessions.
/** The two runs the overview shows: 90 days by day and 12 months by week. */
const HORIZONS = { quarter: "horizon_days=90&granularity=day", year: "horizon_days=365&granularity=week" } as const;
type Horizon = keyof typeof HORIZONS;
function forecast(horizon: Horizon): Promise<Forecast | null> { return fetchForecast(HORIZONS[horizon]); }

const money = (value: { amount: string }) => displayCents(decimalCents(value.amount));

async function fetchForecast(query: string): Promise<Forecast | null> {
  try {
    const response = await apiFetch(`/v1/forecast/liquidity?${query}`, {
      cache: "no-store", signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) return null;
    const body = await response.json();
    if (body.stub !== false || !Array.isArray(body.points) || !body.points.length || !body.lowest_point) return null;
    const start = Date.parse(body.as_of);
    const points = body.points.map((point: { date: string; balance: { amount: string } }) => ({
      day: (Date.parse(point.date) - start) / 86400000, date: point.date, balance: money(point.balance),
    }));
    if (!Number.isFinite(start) || points.some((point: { day: number }) => !Number.isFinite(point.day))) return null;
    return {
      asOf: body.as_of, model: body.model.name, horizonDays: body.horizon_days, openingBalance: money(body.opening_balance),
      safetyBuffer: money(body.safety_buffer), lowest: { date: body.lowest_point.date, balance: money(body.lowest_point.balance) }, points,
    };
  } catch {
    // Forecast availability must not hide the actual bank records.
    return null;
  }
}

export async function getDashboardData(slug?: string): Promise<DashboardData> {
  // Enforce access at the data boundary too, including RSC and route requests.
  const session = await requireSession(slug);
  // The forecast comes from the API, the rest from Postgres: run both at once instead of one after the other.
  const forecastPromises = [forecast("quarter"), forecast("year")] as const;
  const client = await database().connect().catch(() => {
    throw new Error("Company data is temporarily unavailable");
  });
  let result: Omit<DashboardData, "forecast" | "forecastYear">;
  try {
    await client.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
    await client.query("SELECT set_config('search_path',$1,true)", [session.schema]);
    const summary = (await client.query(`
      SELECT to_char(max(booking_date), 'YYYY-MM-DD') AS as_of,
        coalesce((sum(amount) * 100)::bigint, 0)::text AS balance,
        count(*) FILTER (WHERE amount < 0 AND invoice_number IS NOT NULL AND NOT documented)::text AS undocumented
      FROM transactions`)).rows[0];
    summary.as_of ??= new Date().toISOString().slice(0, 10);
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
      SELECT e.id, coalesce(e.slack_name, e.name) AS name, e.role, coalesce(t.name, 'Unassigned') AS team,
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
    // Spend per team this quarter (as of the latest booking) against three monthly budgets; spend without a team
    // (tax, social security, partner payouts) is one extra row. One round trip, under 1 ms on the seeded data.
    const quarter = (await client.query(`
      WITH q AS (SELECT date_trunc('quarter', max(booking_date))::date AS start, max(booking_date)::date AS through FROM transactions),
      spend AS (
        SELECT x.team_id, -sum(x.amount) AS spent FROM transactions x, q
        WHERE x.amount < 0 AND x.booking_date >= q.start GROUP BY x.team_id
      ),
      rows AS (
        SELECT t.id, t.name, (t.monthly_budget * 300)::bigint::text AS budget, (coalesce(s.spent, 0) * 100)::bigint::text AS spent
          FROM teams t LEFT JOIN spend s ON s.team_id = t.id
        UNION ALL
        SELECT NULL, 'Unassigned', NULL, (spent * 100)::bigint::text FROM spend WHERE team_id IS NULL
      )
      SELECT to_char(q.start, 'YYYY-MM-DD') AS start, to_char(q.through, 'YYYY-MM-DD') AS through, rows.*
        FROM q LEFT JOIN rows ON true`)).rows;
    const scout = { ...await loadScout(client), ceoId: session.employeeId, demo: false };
    await client.query("COMMIT");
    result = {
      asOf: summary.as_of, yearLabel: summary.as_of.slice(0, 4),
      year: financePeriod([...grouped.values()]), q4: financePeriod(periods.filter(row => row.period === "q4")),
      closingBalance: displayCents(summary.balance), employees,
      monthlyPayroll: displayCents(payroll.find(row => row.category === "payroll")?.amount ?? "0"),
      monthlyContributions: displayCents(payroll.find(row => row.category === "social_security")?.amount ?? "0"),
      undocumentedPayments: Number(summary.undocumented), scout, generatedAt: new Date().toISOString(),
      quarterSpend: quarterSpend(quarter[0]?.start ?? summary.as_of, quarter[0]?.through ?? summary.as_of, quarter.filter(row => row.name !== null)),
    };
  } catch {
    await client.query("ROLLBACK").catch(() => {});
    // Do not expose SQL, rows or connection strings in errors or logs.
    throw new Error("Company data is temporarily unavailable");
  } finally {
    client.release();
  }
  const [quarterForecast, yearForecast] = await Promise.all(forecastPromises);
  const organizations = await authStore().organizations(session.userId).catch(() => [{ slug: session.slug, name: session.organizationName }]);
  return { ...result, workspace: { organizations, slug: session.slug, name: session.organizationName, userId: session.userId, userName: session.name, role: session.role, googleAvailable: Boolean(process.env.SCOUT_GOOGLE_CLIENT_ID && process.env.SCOUT_GOOGLE_CLIENT_SECRET) }, forecast: quarterForecast, forecastYear: yearForecast };
}

/** Runs read-only queries in one REPEATABLE READ transaction; errors never expose SQL or connection details. */
async function readOnly<T>(work: (client: PoolClient) => Promise<T>): Promise<T> {
  const session = await requireSession();
  const client = await database().connect().catch(() => {
    throw new Error("Company data is temporarily unavailable");
  });
  try {
    await client.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
    await client.query("SELECT set_config('search_path',$1,true)", [session.schema]);
    const value = await work(client);
    await client.query("COMMIT");
    return value;
  } catch {
    await client.query("ROLLBACK").catch(() => {});
    throw new Error("Company data is temporarily unavailable");
  } finally {
    client.release();
  }
}

/** One agent task with its steps, approval, audit rows and linked transaction; null when it does not exist. */
export async function getTaskDetail(id: string): Promise<TaskDetail | null> {
  await requireSession();
  return readOnly(client => loadTaskDetail(client, id));
}

/** The audit log or the transaction list, for the views that show them. */
export async function getRecords<K extends RecordKind>(kind: K): Promise<ScoutRecords[K]> {
  await requireSession();
  return readOnly(client => loadRecords(client, kind));
}
