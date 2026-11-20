use std::collections::BTreeMap;
use std::sync::LazyLock;

const BANK_STATEMENT: &str = include_str!("../data/demo-bank-statement.csv");

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Period {
    Year,
    QuarterFour,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ExpenseCategory {
    pub name: &'static str,
    pub amount: f64,
    pub percentage: f64,
    pub color: &'static str,
}

#[derive(Clone, Debug, PartialEq)]
pub struct FinancialSnapshot {
    pub expenses: Vec<ExpenseCategory>,
    pub total_expenses: f64,
    pub total_revenue: f64,
    pub transactions: usize,
}

static YEAR_SNAPSHOT: LazyLock<FinancialSnapshot> = LazyLock::new(|| build_snapshot(Period::Year));
static Q4_SNAPSHOT: LazyLock<FinancialSnapshot> =
    LazyLock::new(|| build_snapshot(Period::QuarterFour));

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Employee {
    pub name: &'static str,
    pub initials: &'static str,
    pub role: &'static str,
    pub team: &'static str,
    pub salary: f64,
    pub gradient: &'static str,
}

pub const EMPLOYEES: [Employee; 4] = [
    Employee {
        name: "Alex Morgan",
        initials: "AM",
        role: "Owner & General Manager",
        team: "Leadership",
        salary: 3_600.0,
        gradient: "avatar-violet",
    },
    Employee {
        name: "Jordan Lee",
        initials: "JL",
        role: "Pizza Baker",
        team: "Kitchen",
        salary: 2_500.0,
        gradient: "avatar-blue",
    },
    Employee {
        name: "Casey Rivera",
        initials: "CR",
        role: "Chef",
        team: "Kitchen",
        salary: 2_000.0,
        gradient: "avatar-green",
    },
    Employee {
        name: "Taylor Quinn",
        initials: "TQ",
        role: "Waitstaff · Temp",
        team: "Front of house",
        salary: 1_100.0,
        gradient: "avatar-orange",
    },
];

pub fn monthly_payroll() -> f64 {
    EMPLOYEES.iter().map(|employee| employee.salary).sum()
}

pub fn snapshot(period: Period) -> FinancialSnapshot {
    match period {
        Period::Year => YEAR_SNAPSHOT.clone(),
        Period::QuarterFour => Q4_SNAPSHOT.clone(),
    }
}

fn build_snapshot(period: Period) -> FinancialSnapshot {
    let mut totals: BTreeMap<&'static str, f64> = BTreeMap::new();
    let mut total_revenue = 0.0;
    let mut transactions = 0;

    for line in BANK_STATEMENT.lines().skip(1) {
        let fields = split_record(line);
        if fields.len() < 11 {
            continue;
        }

        let month = fields[1]
            .split('.')
            .nth(1)
            .and_then(|part| part.parse::<u8>().ok())
            .unwrap_or_default();

        if period == Period::QuarterFour && month < 10 {
            continue;
        }

        let amount = parse_amount(&fields[8]);
        transactions += 1;

        if amount >= 0.0 {
            total_revenue += amount;
            continue;
        }

        let transaction_type = fields[3].to_ascii_lowercase();
        let reference = fields[4].to_ascii_lowercase();
        let category = categorize(&transaction_type, &reference);
        *totals.entry(category).or_default() += amount.abs();
    }

    let total_expenses: f64 = totals.values().sum();
    let palette = [
        ("Payroll", "#7C6FF2"),
        ("Inventory", "#59A5F5"),
        ("Investment", "#F3A455"),
        ("Rent & utilities", "#42C994"),
        ("Tax & social", "#E86B7D"),
        ("Other", "#8B96A5"),
    ];

    let expenses = palette
        .into_iter()
        .filter_map(|(name, color)| {
            let amount = *totals.get(name)?;
            Some(ExpenseCategory {
                name,
                amount,
                percentage: if total_expenses > 0.0 {
                    amount / total_expenses * 100.0
                } else {
                    0.0
                },
                color,
            })
        })
        .collect();

    FinancialSnapshot {
        expenses,
        total_expenses,
        total_revenue,
        transactions,
    }
}

fn split_record(line: &str) -> Vec<String> {
    line.split(';')
        .map(|field| field.trim_matches('"').replace("\"\"", "\""))
        .collect()
}

fn parse_amount(value: &str) -> f64 {
    value
        .replace('.', "")
        .replace(',', ".")
        .parse::<f64>()
        .unwrap_or_default()
}

fn categorize(transaction_type: &str, reference: &str) -> &'static str {
    if transaction_type.contains("salary") || reference.contains("salary") {
        "Payroll"
    } else if reference.contains("overseas")
        || reference.contains("investment")
        || reference.contains("overseas")
        || transaction_type.contains("international")
    {
        "Investment"
    } else if reference.contains("rent")
        || reference.contains("utilities")
        || reference.contains("energy")
    {
        "Rent & utilities"
    } else if reference.contains("tax") || reference.contains("social security") {
        "Tax & social"
    } else if transaction_type.contains("supplier")
        || transaction_type.contains("giro")
        || reference.contains("produce")
        || reference.contains("ingredients")
        || reference.contains("beverage")
    {
        "Inventory"
    } else {
        "Other"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_german_decimal_amounts() {
        assert_eq!(parse_amount("-4.325,17"), -4_325.17);
        assert_eq!(parse_amount("275,35"), 275.35);
    }

    #[test]
    fn sample_data_produces_synthetic_financial_summary() {
        let year = snapshot(Period::Year);
        assert!(year.transactions > 500);
        assert!(year.total_expenses > 100_000.0);
        assert!(year.total_revenue > 100_000.0);
        assert!(year
            .expenses
            .iter()
            .any(|category| category.name == "Payroll"));
    }

    #[test]
    fn staffing_matches_scenario_payroll() {
        assert_eq!(EMPLOYEES.len(), 4);
        assert_eq!(monthly_payroll(), 9_200.0);
    }
}
