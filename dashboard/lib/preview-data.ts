import "server-only";
import { financePeriod, type DashboardData } from "./finance-model";

// Invented UI fixtures, deliberately independent of private company records.
export function getPreviewData(): DashboardData {
  const period = (scale: number) => financePeriod([
    { category: "revenue", count: "120", income: String(12000000 * scale), expense: "0" },
    { category: "payroll", count: "12", income: "0", expense: String(3600000 * scale) },
    { category: "food", count: "24", income: "0", expense: String(2400000 * scale) },
    { category: "rent", count: "12", income: "0", expense: String(1200000 * scale) },
    { category: "utilities", count: "12", income: "0", expense: String(600000 * scale) },
    { category: "tax", count: "4", income: "0", expense: String(400000 * scale) },
  ]);
  return {
    asOf: "2026-11-20", yearLabel: "2026", year: period(1), q4: period(0.25),
    closingBalance: 38000, monthlyPayroll: 9000, monthlyContributions: 1800,
    undocumentedPayments: 3, pendingApprovals: 1,
    employees: [
      { id: "preview-1", name: "Alex Morgan", initials: "AM", role: "Operations lead", team: "Operations", salary: 3200, avatar: "avatar-green" },
      { id: "preview-2", name: "Sam Taylor", initials: "ST", role: "Team coordinator", team: "Operations", salary: 2800, avatar: "avatar-orange" },
      { id: "preview-3", name: "Jordan Lee", initials: "JL", role: "Finance specialist", team: "Finance", salary: 3000, avatar: "avatar-green" },
      { id: "preview-4", name: "Robin Clark", initials: "RC", role: "New starter", team: "Operations", salary: null, avatar: "avatar-green" },
    ],
    decisions: [
      { id: "preview-approval", ts: "2026-11-20T10:30:00Z", summary: "Review the proposed supplier payment", reason: "This example payment needs an owner’s approval because it exceeds the routine spending threshold.", level: "APPROVAL", status: "pending", executed_simulated: false },
      { id: "preview-document", ts: "2026-11-20T09:15:00Z", summary: "Invoice document matched to a bank booking", reason: "In this sample investigation, the invoice reference and amount match the recorded payment.", level: "AUTO", status: "completed", executed_simulated: true },
    ],
    forecast: {
      asOf: "2026-11-20", model: "Sample forecast", safetyBuffer: 18000,
      points: Array.from({ length: 91 }, (_, day) => ({ day, balance: Math.round(38000 - day * 110 + Math.sin(day / 8) * 2300) })),
    },
  };
}
