"""Salary rows: who may see whose pay (D-029). Pure policy first, then every tool that can return bank rows."""

from __future__ import annotations

from decimal import Decimal

from scout.audit import (can_see_salary, filter_salary_rows, is_salary_row, payee_of, reports_of, salary_sql)
from scout.loops.toolbox import Toolbox
from test_invoicing_loop import TODAY, make_world

PEOPLE = [
    {"id": "vincenzo", "name": "Vincenzo Amalfi", "manager_id": None},
    {"id": "rosa", "name": "Rosa Pavese", "manager_id": "vincenzo"},
    {"id": "grazyna", "name": "Grazyna Nowak", "manager_id": "vincenzo"},
    {"id": "yusuf", "name": "Yusuf Akcit", "manager_id": "rosa"},
    {"id": "vanessa", "name": "Vanessa Paradi", "manager_id": "grazyna"},
]
ROLE = {"vincenzo": "ceo", "rosa": "head_chef", "grazyna": "office_manager", "yusuf": "baker", "vanessa": "waitstaff"}

SALARIES = [  # id, payee name, type, reference, amount, team
    ("sal_v", "Vincenzo Amalfi", "SALARY PAYMENT", "Owner Salary Withdrawal Vincenzo Amalfi 11/2026", "-3200.00", "front"),
    ("sal_y", "Yusuf Akcit", "SALARY PAYMENT", "Net Salary Yusuf Akcit (Pizza Baker) 11/2026", "-2350.00", "kitchen"),
    ("sal_r", "Rosa Pavese", "SALARY PAYMENT", "Net Salary Rosa Pavese (Chef) 11/2026", "-2100.00", "kitchen"),
    ("sal_n", "Vanessa Paradi", "SALARY PAYMENT", "Net Salary Vanessa Paradi (Waitstaff) 11/2026", "-950.00", "front"),
]


def seed_bank(ctx) -> None:  # noqa: ANN001
    with ctx.conn_factory() as conn:
        for tid, who, typ, ref, amt, team in SALARIES:
            conn.execute("INSERT INTO transactions (id, booking_date, type, reference, counterparty, amount, category, "
                         "team_id) VALUES (%s,%s,%s,%s,%s,%s,'payroll',%s)", (tid, TODAY, typ, ref, who, Decimal(amt), team))
        conn.execute("INSERT INTO transactions (id, booking_date, type, reference, counterparty, amount, category, "
                     "team_id) VALUES ('tax1', %s, 'TAX OFFICE TRANSFER', 'Payroll Wage Tax 11/2026', "
                     "'Tax Office Frankfurt am Main', -880, 'tax', NULL)", (TODAY,))
        conn.execute("INSERT INTO transactions (id, booking_date, type, reference, counterparty, amount, category, "
                     "team_id) VALUES ('sup1', %s, 'GIRO', 'Invoice AS-1', 'Asimex Wholesale Ltd', -400, 'food', 'kitchen')",
                     (TODAY,))
        conn.commit()


def box(ctx, who: str) -> Toolbox:  # noqa: ANN001
    t = ctx.recorder.open(f"dm:test:{who}", "chat", "q")
    return Toolbox(ctx, who, ctx.hr.role(who), t)


def amounts(result) -> set[str]:  # noqa: ANN001
    return {r["counterparty"] for r in result["rows"]}


def audit_rows(db):  # noqa: ANN001
    return db.execute("SELECT * FROM audit_log WHERE action = 'salary_filter' ORDER BY id").fetchall()


# -- pure policy ---------------------------------------------------------------------------------------------

def test_detects_salary_rows_but_not_company_payroll_tax():
    assert is_salary_row({"type": "SALARY PAYMENT", "reference": "x", "category": None})
    assert is_salary_row({"type": "x", "reference": "Net Salary Rosa", "category": None})
    assert is_salary_row({"type": "x", "reference": "x", "category": "payroll"})
    assert not is_salary_row({"type": "TAX OFFICE TRANSFER", "reference": "Payroll Wage Tax 11/2026", "category": "tax"})
    assert "salary" in salary_sql("x") and "x.category" in salary_sql("x")


def test_payee_resolution():
    row = {"counterparty": "Rosa Pavese", "reference": "Net Salary Rosa Pavese (Chef)"}
    assert payee_of(row, PEOPLE) == "rosa"
    assert payee_of({"counterparty": "ACME", "reference": "Net Salary Yusuf Akcit"}, PEOPLE) == "yusuf"
    assert payee_of({"counterparty": "ACME", "reference": "Net Salary", "employee_id": "vanessa"}, PEOPLE) == "vanessa"
    assert payee_of({"counterparty": "Nobody Known", "reference": "Net Salary Nobody Known"}, PEOPLE) is None
    assert payee_of({"counterparty": "X", "reference": "Salary Rosa Pavese and Yusuf Akcit"}, PEOPLE) is None


def test_reports_follow_the_manager_chain():
    assert reports_of("vincenzo", PEOPLE) == {"rosa", "grazyna", "yusuf", "vanessa"}
    assert reports_of("rosa", PEOPLE) == {"yusuf"}
    assert reports_of("grazyna", PEOPLE) == {"vanessa"}
    assert reports_of("yusuf", PEOPLE) == set()


def test_can_see_salary_matrix():
    assert can_see_salary("vincenzo", "ceo", "rosa")[0] is True
    assert can_see_salary("vincenzo", "ceo", None)[0] is True
    assert can_see_salary("rosa", "head_chef", "rosa")[0] is True
    assert can_see_salary("rosa", "head_chef", "yusuf", {"yusuf"})[0] is True
    assert can_see_salary("rosa", "head_chef", "vanessa", {"yusuf"})[0] is False
    assert can_see_salary("rosa", "head_chef", "vincenzo", {"yusuf"})[0] is False
    assert can_see_salary("vanessa", "waitstaff", "yusuf")[0] is False
    assert can_see_salary("yusuf", "baker", None)[0] is False  # unknown payee = someone else's pay
    assert can_see_salary("grazyna", "office_manager", "rosa")[0] is False  # masked, not shown, for the office manager


def test_filter_masks_for_office_manager_and_denies_for_others():
    rows = [dict(type=t, reference=r, counterparty=c, amount=Decimal(a), category="payroll", id=i)
            for i, c, t, r, a, _ in SALARIES]
    rows.append(dict(type="GIRO", reference="Invoice", counterparty="Asimex", amount=Decimal("-400"), category="food", id="s"))
    fm = filter_salary_rows(rows, "grazyna", "office_manager", PEOPLE)
    assert [r["id"] for r in fm.visible] == ["sal_n", "s"]  # her report Vanessa and the non-salary row
    assert fm.summary == "Payroll (3 employees): 7,650.00 EUR"
    assert {d for _, d, _ in fm.hidden} == {"MASK"}
    rosa = filter_salary_rows(rows, "rosa", "head_chef", PEOPLE)
    assert [r["id"] for r in rosa.visible] == ["sal_y", "sal_r", "s"] and rosa.summary is None
    assert {d for _, d, _ in rosa.hidden} == {"DENY"}
    ceo = filter_salary_rows(rows, "vincenzo", "ceo", PEOPLE)
    assert len(ceo.visible) == 5 and ceo.summary is None and ceo.hidden == []
    waiter = filter_salary_rows(rows, "vanessa", "waitstaff", PEOPLE)
    assert [r["id"] for r in waiter.visible] == ["sal_n", "s"] and waiter.summary is None


def test_unknown_payee_is_someone_elses_pay():
    row = dict(type="SALARY PAYMENT", reference="Net Salary Stranger", counterparty="Stranger", amount=Decimal("-1"),
               category="payroll", id="x")
    for who, role in (("rosa", "head_chef"), ("vanessa", "waitstaff")):
        assert filter_salary_rows([row], who, role, PEOPLE).visible == []
    # a lone hidden payee is dropped, never summed: an "aggregate" of one person is that person's pay
    lone = filter_salary_rows([row], "grazyna", "office_manager", PEOPLE)
    assert lone.visible == [] and lone.summary is None and {d for _, d, _ in lone.hidden} == {"DENY"}
    two = filter_salary_rows([row, {**row, "id": "y", "counterparty": "Other", "reference": "Net Salary Other"}],
                             "grazyna", "office_manager", PEOPLE)
    assert two.summary == "Payroll (2 employees): 2.00 EUR"


# -- tools ---------------------------------------------------------------------------------------------------

def test_search_payments_ceo_sees_every_salary_row(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    seed_bank(ctx)
    out = box(ctx, "vincenzo").call("search_payments", {})
    assert {"Vincenzo Amalfi", "Yusuf Akcit", "Rosa Pavese", "Vanessa Paradi", "Asimex Wholesale Ltd"} <= amounts(out)
    assert "payroll_summary" not in out and audit_rows(db) == []


def test_search_payments_manager_sees_own_and_reports(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    seed_bank(ctx)
    out = box(ctx, "rosa").call("search_payments", {})
    assert amounts(out) == {"Yusuf Akcit", "Rosa Pavese"}  # no general bank access: only salary rows she may see
    assert "payroll_summary" not in out
    assert audit_rows(db) == []  # others' rows are never even fetched for someone without bank access
    # asking for someone else by name gets nothing
    assert box(ctx, "rosa").call("search_payments", {"counterparty": "Vincenzo"})["error"] == "access_denied"
    assert box(ctx, "rosa").call("search_payments", {"amount_eur": "3200"})["error"] == "access_denied"


def test_search_payments_staff_sees_only_own_row(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    seed_bank(ctx)
    out = box(ctx, "yusuf").call("search_payments", {})
    assert amounts(out) == {"Yusuf Akcit"}
    assert box(ctx, "yusuf").call("search_payments", {"counterparty": "Rosa"})["error"] == "access_denied"
    # Grazyna is not on payroll: nothing of hers, and no general bank access for staff
    assert box(ctx, "vanessa").call("search_payments", {"counterparty": "Asimex"})["error"] == "access_denied"
    assert "payroll_summary" not in out


def test_search_payments_office_manager_gets_one_payroll_line(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    seed_bank(ctx)
    out = box(ctx, "grazyna").call("search_payments", {})
    assert amounts(out) == {"Vanessa Paradi", "Asimex Wholesale Ltd", "Tax Office Frankfurt am Main"}
    assert out["payroll_summary"] == "Payroll (3 employees): 7,650.00 EUR"
    blob = str(out)
    for secret in ("3200", "2350", "2100", "Vincenzo", "Yusuf", "Rosa"):
        assert secret not in blob
    log = audit_rows(db)
    assert len(log) == 3 and {r["decision"] for r in log} == {"MASK"} and all(r["policy"] for r in log)
    assert all(r["requester"] == "grazyna" for r in log)


def test_specific_search_cannot_pry_a_salary_out(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    seed_bank(ctx)
    for args in ({"counterparty": "Yusuf"}, {"amount_eur": "2350"}, {"counterparty": "Rosa", "days": 1}):
        out = box(ctx, "grazyna").call("search_payments", args)
        assert out["rows"] == []
        # the aggregate covers everyone hidden in the period, it is never narrowed to the person asked about
        assert out["payroll_summary"] == "Payroll (3 employees): 7,650.00 EUR"
        assert "Yusuf" not in str(out) and "2350" not in str(out) and "2,350" not in str(out)
    # a lone remaining payee must not be summed either
    with ctx.conn_factory() as conn:
        conn.execute("DELETE FROM transactions WHERE id IN ('sal_y', 'sal_r')")
        conn.commit()
    out = box(ctx, "grazyna").call("search_payments", {})
    assert "payroll_summary" not in out and "Vincenzo" not in str(out) and "3200" not in str(out)


def test_missing_invoices_never_carries_salary_rows(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    seed_bank(ctx)
    with ctx.conn_factory() as conn:  # a salary row that (wrongly) carries an invoice-like number
        conn.execute("UPDATE transactions SET invoice_number = 'AS-99999' WHERE id IN ('sal_y', 'sal_v')")
        conn.commit()
    out = box(ctx, "grazyna").call("missing_invoices", {})
    assert "Yusuf" not in str(out) and "Vincenzo" not in str(out) and "2350" not in str(out)
    assert out["payroll_summary"] == "Payroll (2 employees): 5,550.00 EUR"
    assert {r["decision"] for r in audit_rows(db)} == {"MASK"}
    ceo = box(ctx, "vincenzo").call("missing_invoices", {})
    assert {r["counterparty"] for r in ceo["payments_without_invoice"]} >= {"Yusuf Akcit", "Vincenzo Amalfi"}
    assert box(ctx, "yusuf").call("missing_invoices", {})["error"] == "access_denied"


def test_cash_position_stays_bank_gated_and_has_no_per_person_pay(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    seed_bank(ctx)
    for who in ("rosa", "yusuf", "vanessa"):
        assert box(ctx, who).call("cash_position", {})["error"] == "access_denied"
    out = box(ctx, "grazyna").call("cash_position", {})
    assert set(out) == {"balance", "money_in_30d", "money_out_30d", "expected"}  # totals only, no rows


def test_cost_trends_has_no_payroll_series(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    seed_bank(ctx)
    with ctx.conn_factory() as conn:
        conn.execute("INSERT INTO cost_metrics (id, kind, month, cost, metric_value, team_id) VALUES "
                     "('cm1', 'food_cost', %s, 1000, 100, 'kitchen')", (TODAY.replace(day=1),))
        conn.commit()
    out = box(ctx, "yusuf").call("cost_trends", {})
    assert list(out["series"]) == ["food_cost"] and "2350" not in str(out)


def test_team_budgets_never_reveal_others_pay(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    seed_bank(ctx)

    def spent(who: str) -> dict[str, str]:
        res = box(ctx, who).call("team_budgets", {})
        return {r["team"]: r["spent_this_month"] for r in res["rows"]}

    ceo = spent("vincenzo")
    assert Decimal(ceo["kitchen"]) == Decimal("4850.00") and Decimal(ceo["front"]) == Decimal("4150.00")  # incl. pay
    waiter = spent("vanessa")  # only her own 950 on top of non-salary spend
    assert Decimal(waiter["front"]) == Decimal("950.00")
    assert Decimal(waiter["kitchen"]) == Decimal("400.00")
    yusuf = spent("yusuf")
    assert Decimal(yusuf["kitchen"]) == Decimal("2750.00")  # 400 supplier + his own 2350
    rosa = spent("rosa")
    assert Decimal(rosa["kitchen"]) == Decimal("4850.00")  # she sees herself and her report Yusuf
    assert Decimal(spent("grazyna")["kitchen"]) == Decimal("400.00") and Decimal(spent("grazyna")["front"]) == Decimal("950.00")
    assert any(r["decision"] == "MASK" for r in audit_rows(db))  # Grazyna: aggregate
    note = box(ctx, "grazyna").call("team_budgets", {})
    assert "payroll_summary" in note and "Yusuf" not in str(note)
