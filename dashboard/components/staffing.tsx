"use client";

import { useState } from "react";
import { Download, Lock, Search } from "lucide-react";
import type { DashboardData } from "@/lib/finance";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export type StaffingProps = {
  data: DashboardData;
  onBack: () => void;
};

const currency = new Intl.NumberFormat("en-IE", {
  style: "currency",
  currency: "EUR",
  minimumFractionDigits: 2,
});

function csvCell(value: string | number): string {
  return `"${String(value).replace(/"/g, '""')}"`;
}

export function Staffing({ data, onBack }: StaffingProps) {
  const [search, setSearch] = useState("");
  const [exportNotice, setExportNotice] = useState("");
  const [analysisOpen, setAnalysisOpen] = useState(false);
  const query = search.trim().toLocaleLowerCase();
  const people = data.employees.filter((employee) =>
    [employee.name, employee.role, employee.team]
      .join(" ")
      .toLocaleLowerCase()
      .includes(query),
  );

  const exportPayroll = () => {
    const header = [
      "Employee",
      "Role",
      "Team",
      "Monthly net salary",
      "Currency",
      "Payment day",
      "Status",
      "Source",
    ];
    const rows = data.employees.map((employee) => [
      employee.name,
      employee.role,
      employee.team,
      employee.salary?.toFixed(2) ?? "",
      "EUR",
      "Not scheduled",
      "Active",
      "Company database · latest recorded payroll",
    ]);
    const csv = [header, ...rows]
      .map((row) => row.map(csvCell).join(","))
      .join("\r\n");
    const url = URL.createObjectURL(
      new Blob(["\uFEFF", csv, "\r\n"], { type: "text/csv;charset=utf-8;" }),
    );
    const link = document.createElement("a");
    link.id = "scout-payroll-download-link";
    link.href = url;
    link.download = "scout-payroll.csv";
    link.hidden = true;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    setExportNotice(
      `Payroll CSV prepared for ${data.employees.length} people. The export includes the whole team and includes the latest recorded monthly payments; missing payments are blank.`,
    );
  };

  return (
    <div id="scout-staffing" className="space-y-6">
      <section
        id="scout-staffing-heading"
        aria-labelledby="scout-staffing-title"
        className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between"
      >
        <div className="space-y-2">
          <p className="font-mono text-xs uppercase tracking-wide text-muted-foreground">
            People operations
          </p>
          <h1
            id="scout-staffing-title"
            className="text-2xl font-medium tracking-tight md:text-3xl"
          >
            Staffing &amp; payroll
          </h1>
          <p className="max-w-2xl text-sm leading-6 text-muted-foreground">
            A current view of people, responsibilities and recurring salary commitments.
          </p>
        </div>
        <Button id="scout-staffing-back" type="button" variant="outline" className="min-h-11 shrink-0" onClick={onBack}>
          Back to overview
        </Button>
      </section>

      <section
        id="scout-staffing-summary"
        aria-label="Staffing summary"
        className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"
      >
        {[
          {
            id: "people",
            label: "Active team",
            value: String(data.employees.length),
            note: "Profiles in the company directory",
          },
          {
            id: "net",
            label: "Latest recorded payroll",
            value: currency.format(data.monthlyPayroll),
            note: "Latest month with salary bookings",
          },
          {
            id: "contributions",
            label: "Employer contributions",
            value: currency.format(data.monthlyContributions),
            note: "Social security · monthly",
          },
          {
            id: "scheduled",
            label: "Records through",
            value: data.asOf,
            note: "Latest bank booking date",
          },
        ].map((metric) => (
          <Card id={`scout-payroll-metric-${metric.id}`} key={metric.id}>
            <CardContent className="space-y-2 pt-5">
              <p className="text-sm text-muted-foreground">{metric.label}</p>
              <p className="break-words font-mono text-2xl tracking-tight">{metric.value}</p>
              <p className="text-xs leading-5 text-muted-foreground">{metric.note}</p>
            </CardContent>
          </Card>
        ))}
      </section>

      <Card id="scout-staff-directory" aria-labelledby="scout-team-title" className="min-w-0">
        <CardHeader className="gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="space-y-1.5">
            <CardTitle id="scout-team-title" className="text-base font-medium">
              Team directory
            </CardTitle>
            <CardDescription>
              Latest recorded monthly salary payments matched by employee name
            </CardDescription>
          </div>
          <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center">
            <div className="relative min-w-0 sm:w-56">
              <label htmlFor="scout-team-search" className="sr-only">
                Search team by name, role or team
              </label>
              <Search className="pointer-events-none absolute left-3 top-3.5 size-4 text-muted-foreground" aria-hidden="true" />
              <Input
                id="scout-team-search"
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search people"
                aria-controls="scout-payroll-table"
                className="min-h-11 pl-9 text-base sm:text-sm"
              />
            </div>
            <Button
              id="scout-payroll-export"
              type="button"
              variant="secondary"
              className="min-h-11 shrink-0"
              onClick={exportPayroll}
            >
              <Download className="size-4" aria-hidden="true" />
              Export payroll
            </Button>
          </div>
        </CardHeader>

        <CardContent className="min-w-0 space-y-3 px-0 pb-0">
          {exportNotice && (
            <p id="scout-payroll-export-status" role="status" className="px-6 text-sm leading-6 text-success">
              {exportNotice}
            </p>
          )}
          <div id="scout-payroll-table-scroll" className="max-w-full overflow-x-auto">
            <Table id="scout-payroll-table" className="min-w-[680px]">
              <TableCaption className="sr-only">Latest recorded monthly salary payments in EUR. Missing payments are not recorded.</TableCaption>
              <TableHeader>
                <TableRow className="bg-muted/25 hover:bg-muted/25">
                  <TableHead className="pl-6">Employee</TableHead>
                  <TableHead>Team</TableHead>
                  <TableHead className="text-right">Monthly net</TableHead>
                  <TableHead className="pr-6">Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {people.length > 0 ? people.map((employee) => (
                  <TableRow id={`scout-employee-${employee.id}`} key={employee.id}>
                    <TableCell className="py-5 pl-6">
                      <div className="flex items-center gap-3">
                        <Avatar className="size-10 shrink-0" aria-hidden="true">
                          <AvatarFallback className={employee.avatar === "avatar-orange" ? "bg-warning-muted text-warning" : "bg-success-muted text-success"}>
                            {employee.initials}
                          </AvatarFallback>
                        </Avatar>
                        <div className="space-y-1">
                          <p className="text-sm font-medium">{employee.name}</p>
                          <p className="text-xs text-muted-foreground">{employee.role}</p>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">{employee.team}</TableCell>
                    <TableCell className="text-right">
                      <p className="font-mono text-sm tabular-nums">{employee.salary === null ? "Not recorded" : currency.format(employee.salary)}</p>
                      <p className="mt-1 text-xs text-muted-foreground">€ · monthly</p>
                    </TableCell>
                    <TableCell className="pr-6">
                      <Badge variant="outline" className="border-success-border bg-success-muted font-normal text-success">Active</Badge>
                    </TableCell>
                  </TableRow>
                )) : (
                  <TableRow id="scout-team-empty">
                    <TableCell colSpan={4} className="h-32 px-6 text-center">
                      <p className="text-sm">No team members match “{search.trim()}”.</p>
                      <Button id="scout-team-clear-search" type="button" variant="ghost" onClick={() => setSearch("")} className="mt-2 min-h-10">Clear search</Button>
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
        <CardFooter className="flex flex-wrap items-center justify-between gap-3 border-t pt-4 text-xs leading-5 text-muted-foreground">
          <p className="flex items-center gap-2"><Lock className="size-3.5 shrink-0" aria-hidden="true" />Protected company workspace · Read-only records</p>
          <p id="scout-team-result-count" role="status" aria-atomic="true">
            {people.length} of {data.employees.length} people
          </p>
        </CardFooter>
      </Card>

      <Card id="scout-payroll-insight" className="border-success-border bg-success-muted" aria-labelledby="scout-payroll-insight-title">
        <CardHeader className="gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-2">
            <CardTitle id="scout-payroll-insight-title" className="text-base font-medium">CFO insight</CardTitle>
            <CardDescription className="max-w-3xl leading-6">
              The latest salary month contains {currency.format(data.monthlyPayroll)} in monthly net payroll.
              Review these recorded payments alongside other operating expenses.
            </CardDescription>
          </div>
          <Button id="scout-payroll-analysis-toggle" type="button" variant="ghost" className="min-h-11 shrink-0" aria-expanded={analysisOpen} aria-controls="scout-payroll-analysis" onClick={() => setAnalysisOpen(!analysisOpen)}>
            {analysisOpen ? "Close analysis" : "Open analysis"}
          </Button>
        </CardHeader>
        {analysisOpen && (
          <CardContent id="scout-payroll-analysis" className="space-y-2 text-sm leading-6 text-muted-foreground">
            <p>Individual payments are matched to employee names in the bank records. A missing match is shown as “Not recorded”; it is not treated as a zero salary.</p>
            <p>Employer social contributions of {currency.format(data.monthlyContributions)} are separate from the {currency.format(data.monthlyPayroll)} monthly net payroll above. These are recorded payments, not a scheduled payroll forecast.</p>
          </CardContent>
        )}
      </Card>
    </div>
  );
}

export default Staffing;
