import "server-only";
import { headers } from "next/headers";
import { Pool } from "pg";
import { authorized } from "./auth";
import { isDevelopmentPreview } from "./development-preview";
import { decimalCents, displayCents, financePeriod, type DashboardData, type Forecast } from "./finance-model";
import { loadScout, loadTaskDetail } from "./scout";
import type { TaskDetail } from "./scout-model";
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

// The forecast is the same for every viewer and only moves when bookings change, but the API runs the model on
// every call (100-250 ms). Cache it in-process: fresh for 60 s, then served stale while one refresh runs.
const FORECAST_FRESH_MS = 60_000;
const FORECAST_STALE_MS = 15 * 60_000;
let forecastCache: { value: Forecast | null; at: number } | undefined;
let forecastInFlight: Promise<Forecast | null> | undefined;

function forecast(): Promise<Forecast | null> {
  const age = forecastCache ? Date.now() - forecastCache.at : Infinity;
  if (age < FORECAST_FRESH_MS) return Promise.resolve(forecastCache!.value);
  forecastInFlight ??= fetchForecast().then(value => {
    // A failed refresh keeps the last good forecast until it is too old to show.
    if (value || !forecastCache || Date.now() - forecastCache.at > FORECAST_STALE_MS) forecastCache = { value, at: Date.now() };
    return forecastCache?.value ?? value;
  }).finally(() => { forecastInFlight = undefined; });
  return age < FORECAST_STALE_MS && forecastCache?.value ? Promise.resolve(forecastCache.value) : forecastInFlight;
}

async function fetchForecast(): Promise<Forecast | null> {
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

export async function getDashboardData(): Promise<DashboardData> {
  // Enforce access at the data boundary too, including RSC and route requests.
  if (!authorized((await headers()).get("authorization"))) throw new Error("Access denied");
  if (isDevelopmentPreview()) return (await import("./preview-data")).getPreviewData();
  // The forecast comes from the API, the rest from Postgres: run both at once instead of one after the other.
  const forecastPromise = forecast();
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
    const scout = await loadScout(client);
    await client.query("COMMIT");
    result = {
      asOf: summary.as_of, yearLabel: summary.as_of.slice(0, 4),
      year: financePeriod([...grouped.values()]), q4: financePeriod(periods.filter(row => row.period === "q4")),
      closingBalance: displayCents(summary.balance), employees,
      monthlyPayroll: displayCents(payroll.find(row => row.category === "payroll")?.amount ?? "0"),
      monthlyContributions: displayCents(payroll.find(row => row.category === "social_security")?.amount ?? "0"),
      undocumentedPayments: Number(summary.undocumented), scout,
    };
  } catch {
    await client.query("ROLLBACK").catch(() => {});
    // Do not expose SQL, rows or connection strings in errors or logs.
    throw new Error("Company data is temporarily unavailable");
  } finally {
    client.release();
  }
  return { ...result, forecast: await forecastPromise };
}

/** One agent task with its steps, approval, audit rows and linked transaction; null when it does not exist. */
export async function getTaskDetail(id: string): Promise<TaskDetail | null> {
  if (!authorized((await headers()).get("authorization"))) throw new Error("Access denied");
  if (isDevelopmentPreview()) return (await import("./preview-data")).getPreviewTask(id);
  const client = await database().connect().catch(() => {
    throw new Error("Company data is temporarily unavailable");
  });
  try {
    await client.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
    const detail = await loadTaskDetail(client, id);
    await client.query("COMMIT");
    return detail;
  } catch {
    await client.query("ROLLBACK").catch(() => {});
    throw new Error("Company data is temporarily unavailable");
  } finally {
    client.release();
  }
}
