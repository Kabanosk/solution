export type MarketResearchQuery = { role: string; location: string; seniority?: string };
export type PublicSource = { title: string; url: string };
export type PublicResearch = { summary: string; sources: PublicSource[]; researched_at: string };

function boundedText(value: unknown, max: number, label: string): string {
  if (typeof value !== "string" || !value.trim() || value.length > max || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value))
    throw new Error(`Invalid ${label}.`);
  return value.trim();
}

export function parseMarketResearchQuery(value: unknown): MarketResearchQuery {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Specify a role and location for public research.");
  const row = value as Record<string, unknown>;
  if (Object.keys(row).some(key => !["role", "location", "seniority"].includes(key))) throw new Error("Unsupported research parameters.");
  const query: MarketResearchQuery = { role: boundedText(row.role, 100, "job role"), location: boundedText(row.location, 120, "job location") };
  if (row.seniority !== undefined) query.seniority = boundedText(row.seniority, 80, "seniority");
  if (Object.values(query).some(text => /[\r\n\t]/.test(text))) throw new Error("Research criteria must be single-line text.");
  return query;
}

/** Public citation destinations only. The application never fetches these URLs. */
export function publicSourceUrl(value: unknown): string {
  if (typeof value !== "string" || value.length > 2000 || /[\u0000-\u0020\u007f]/.test(value)) throw new Error("Invalid public source URL.");
  const url = new URL(value);
  const hostname = url.hostname.toLowerCase().replace(/\.$/, "");
  if (url.protocol !== "https:" || url.username || url.password || url.port || !hostname.includes(".")
    || /^[\d.]+$/.test(hostname) || hostname.includes(":") || hostname.includes("[")
    || /(?:^|\.)(?:localhost|local|internal|svc|test|invalid|example|onion|localdomain)$/.test(hostname))
    throw new Error("Research sources must be public HTTPS pages.");
  return url.href;
}

export function parsePublicResearch(value: unknown): PublicResearch {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid public research.");
  const row = value as Record<string, unknown>;
  if (!Array.isArray(row.sources) || row.sources.length < 1 || row.sources.length > 8) throw new Error("Public research needs 1–8 cited sources.");
  const sources = row.sources.map((value): PublicSource => {
    if (!value || typeof value !== "object") throw new Error("Invalid research source.");
    const source = value as Record<string, unknown>;
    return { title: boundedText(source.title, 200, "source title"), url: publicSourceUrl(source.url) };
  });
  const researched_at = boundedText(row.researched_at, 40, "research timestamp");
  if (!/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(researched_at) || !Number.isFinite(Date.parse(researched_at))) throw new Error("Invalid research timestamp.");
  return { summary: boundedText(row.summary, 8000, "research summary"), sources, researched_at };
}

export type HiringProposal = {
  role: string; location: string; headcount: number; monthly_salary_eur: string;
  salary_range_min_eur: string; salary_range_max_eur: string; start_month: string;
  duration_months: number; employer_cost_percent: string;
};
export type HiringIntent = HiringProposal & { research: PublicResearch };

function money(value: unknown, max: number): string {
  if ((typeof value !== "string" && typeof value !== "number") || !/^\d{1,10}(?:\.\d{1,2})?$/.test(String(value)) || Number(value) <= 0 || Number(value) > max)
    throw new Error("Use a positive monthly EUR amount with at most two decimals.");
  return String(value);
}

/** Research is injected from this request's actual public tool result, never from model arguments. */
export function parseHiringProposal(value: unknown, research: PublicResearch | undefined, query: MarketResearchQuery | undefined): HiringIntent {
  if (!research || !query) throw new Error("Research the role and location before requesting a hiring scenario.");
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid hiring scenario.");
  const row = value as Record<string, unknown>;
  if (Object.keys(row).some(key => !["role", "location", "headcount", "monthly_salary_eur", "salary_range_min_eur", "salary_range_max_eur", "start_month", "duration_months", "employer_cost_percent"].includes(key)))
    throw new Error("Unsupported hiring scenario parameters.");
  const role = boundedText(row.role, 100, "job role"), location = boundedText(row.location, 120, "job location");
  if (role.toLowerCase() !== query.role.toLowerCase() || location.toLowerCase() !== query.location.toLowerCase())
    throw new Error("Hiring role and location must match the public research.");
  if (typeof row.headcount !== "number" || !Number.isInteger(row.headcount) || row.headcount < 1 || row.headcount > 1000) throw new Error("Choose 1–1,000 people for the scenario.");
  if (typeof row.duration_months !== "number" || !Number.isInteger(row.duration_months) || row.duration_months < 1 || row.duration_months > 12) throw new Error("Choose a scenario duration of 1–12 months.");
  if (typeof row.start_month !== "string" || !/^20\d{2}-(?:0[1-9]|1[0-2])$/.test(row.start_month)) throw new Error("Specify the hiring start month as YYYY-MM.");
  const salary = money(row.monthly_salary_eur, 1e6), min = money(row.salary_range_min_eur, 1e6), max = money(row.salary_range_max_eur, 1e6);
  if (Number(min) > Number(salary) || Number(salary) > Number(max)) throw new Error("The proposed salary must be inside the estimated range.");
  const percent = row.employer_cost_percent ?? "0";
  if ((typeof percent !== "string" && typeof percent !== "number") || !/^\d{1,3}(?:\.\d{1,2})?$/.test(String(percent)) || Number(percent) < 0 || Number(percent) > 100)
    throw new Error("Employer costs must be an explicit percentage from 0 to 100.");
  if (row.headcount * Number(salary) * (1 + Number(percent) / 100) * row.duration_months > 1e9) throw new Error("The hiring scenario exceeds the maximum total cost.");
  return { role, location, headcount: row.headcount, monthly_salary_eur: salary, salary_range_min_eur: min,
    salary_range_max_eur: max, start_month: row.start_month, duration_months: row.duration_months,
    employer_cost_percent: String(percent), research: parsePublicResearch(research) };
}
