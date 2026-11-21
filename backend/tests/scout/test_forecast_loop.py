from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cfo.api.errors import ApiError
from cfo.tenancy import LEGACY_ID, LEGACY_SLUG, Tenant, bind_tenant
from scout import api
from scout import forecast_plan as fp
from scout.loops import forecast
from scout.mailer import LogMailer
from scout.models import Job
from test_invoicing_loop import make_world

OWNER = Tenant(id=LEGACY_ID, slug=LEGACY_SLUG, schema_name='public', employee_id='vincenzo', role='owner', legacy=True)
STAFF = Tenant(id=LEGACY_ID, slug=LEGACY_SLUG, schema_name='public', employee_id='vanessa', role='member', legacy=True)


def _txn(db, id_, day, amount, category, team=None, counterparty='customers', documented=True):
    db.execute("INSERT INTO transactions(id,booking_date,type,reference,counterparty,amount,currency,category,team_id,"
               "documented) VALUES (%s,%s,'x','',%s,%s,'EUR',%s,%s,%s)",
               (id_, day, counterparty, Decimal(amount), category, team, documented))


def _seed_year(db):
    # Nov 2025 .. Oct 2026: revenue 10k growing 1%/month; marketing spend doubling in the last 6 months.
    for i in range(12):
        m = fp.month_start(date(2026, 11, 1), i - 12)
        _txn(db, f'rev-{i}', m + timedelta(days=14), str(round(10000 * 1.01 ** i, 2)), 'revenue')
        _txn(db, f'mkt-{i}', m + timedelta(days=3), '-1000' if i < 6 else '-2000', 'marketing', 'marketing', 'AdNet')
    _txn(db, 'nodoc-1', date(2026, 10, 20), '-450', 'office', 'front', 'Unknown GmbH', documented=False)
    db.commit()


def _job(kind, key, payload=None):
    return Job(id=1, kind=kind, payload=payload or {}, dedupe_key=key, status='running', attempts=1,
               run_after=datetime(2026, 11, 20, tzinfo=timezone.utc))


# -- pure maths --------------------------------------------------------------------------------------------------
def test_default_target_is_150_percent_of_trailing_revenue():
    rows = [{'booking_date': date(2026, m, 5), 'amount': Decimal('1000'), 'category': 'revenue'} for m in range(1, 11)]
    rows.append({'booking_date': date(2026, 11, 2), 'amount': Decimal('9999'), 'category': 'revenue'})  # current month
    rows.append({'booking_date': date(2026, 3, 2), 'amount': Decimal('-500'), 'category': 'rent'})
    assert fp.trailing_revenue(rows, date(2026, 11, 20)) == Decimal('10000.00')
    assert fp.default_target(Decimal('10000')) == Decimal('15000.00')


def test_target_path_adds_up_and_forecast_trend_is_capped():
    history = [Decimal(1000 * 2 ** i) for i in range(12)]  # +100%/month must not be extrapolated
    assert fp.trend(history) == fp.MAX_TREND
    steady = [Decimal(10000)] * 12
    path = fp.target_path(steady, Decimal('180000'))
    assert sum(path) == Decimal('180000.00') and path[0] < path[-1] and min(path) > 0
    below = fp.target_path(history, Decimal('2000000'))  # target under today's run rate: flat, never negative
    assert sum(below) == Decimal('2000000.00') and min(below) > 0
    assert len(fp.project(history)) == 12


def test_parse_rejects_bad_settings():
    assert fp.parse({'annual_revenue_target': '150000', 'no_hiring': True}).no_hiring
    for bad in ({'annual_revenue_target': '-1'}, {'annual_revenue_target': 'x'},
                {'annual_revenue_target': '1', 'max_new_monthly_cost': '-5'}, {'annual_revenue_target': '1', 'enabled': 'yes'}):
        with pytest.raises(ValueError):
            fp.parse(bad)


# -- review ------------------------------------------------------------------------------------------------------
def test_review_defaults_to_150_percent_and_opens_team_tasks(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    _seed_year(db)
    task = forecast.review(ctx, _job('forecast_review', 'forecast:2026-11-20', {'day': '2026-11-20'}))
    assert task.loop == 'forecast' and task.status == 'done' and task.category == 'forecast_review'
    report = task.evidence[0]
    assert report['kind'] == 'forecast_plan' and report['target_source'] == 'default'
    assert Decimal(report['target_annual']) == fp.money(Decimal(report['trailing_revenue']) * Decimal('1.5'))
    assert len(report['months']) == 24 and report['months'][0]['actual'] and report['months'][12]['target']
    assert Decimal(report['gap']) < 0  # 1% a month never reaches +50%
    areas = [w['area'] for w in report['weak_points']]
    assert 'Revenue growth' in areas and 'Marketing costs' in areas and 'Payments without documents' in areas
    assert {s['name'] for s in report['sources']} == {'bank', 'invoices', 'email', 'slack'}
    actions = db.execute("SELECT * FROM agent_tasks WHERE category='forecast_action'").fetchall()
    assert len(actions) == len(report['actions']) >= 3 and all(a['status'] == 'todo' for a in actions)
    assert any(a['owner_id'] == 'vanessa' for a in actions)  # marketing team owner
    assert any(m['to'] == 'vanessa' and 'Forecast plan' in m['text'] for m in ctx.notifier.sent)
    # Idempotent: a second run of the same job returns the same review and opens no new actions.
    assert forecast.review(ctx, _job('forecast_review', 'forecast:2026-11-20', {'day': '2026-11-20'})).id == task.id
    again = forecast.review(ctx, _job('forecast_review', 'forecast:2026-11-20:manual', {'day': '2026-11-20'}))
    assert again.id != task.id
    assert db.execute("SELECT count(*) AS n FROM agent_tasks WHERE category='forecast_action'").fetchone()['n'] == len(actions)


def test_review_uses_ceo_target_and_skips_when_paused(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    _seed_year(db)
    fp.save(db, fp.Plan(Decimal('500000'), enabled=False), 'vincenzo')
    db.commit()
    assert forecast.review(ctx, _job('forecast_review', 'forecast:2026-11-20', {'day': '2026-11-20', 'scheduled': True})) is None
    task = forecast.review(ctx, _job('forecast_review', 'forecast:m1', {'day': '2026-11-20'}))
    assert task.evidence[0]['target_source'] == 'ceo' and task.evidence[0]['target_annual'] == '500000.00'


# -- drift -------------------------------------------------------------------------------------------------------
def test_hiring_against_the_plan_alerts_ceo_on_slack_and_email(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    ctx.mailer = LogMailer()
    ctx.settings = ctx.settings.model_copy(update={'ceo_email': 'ceo@amalfi.test'})
    fp.save(db, fp.Plan(Decimal('200000'), no_hiring=True, guidance='No new hires until Q2.'), 'vincenzo')
    db.commit()
    watcher = forecast.DriftWatcher(conn_factory, ctx.jobs)
    assert watcher.poll() == 0  # first look only sets the cursor
    hire = ctx.recorder.open('web-hiring:vanessa:c1:abc', 'liquidity', 'Hire a second waiter from January',
                             requested_by='vanessa', amount=Decimal('-3200'), simulated=True)
    ctx.recorder.open('txn:ok', 'invoicing', 'Card payment Metro', requested_by='agent')
    assert watcher.poll() == 1 and watcher.poll() == 0
    job = db.execute("SELECT * FROM agent_jobs WHERE kind='forecast_drift'").fetchone()
    assert job['payload'] == {'rule': 'no_hiring', 'task_id': hire.id}
    alert = forecast.drift(ctx, _job('forecast_drift', job['dedupe_key'], job['payload']))
    assert alert.category == 'drift' and alert.status == 'escalated' and alert.title.startswith('URGENT')
    to = {m['to']: m['text'] for m in ctx.notifier.sent}
    assert 'Why was this done?' in to['vanessa'] and 'URGENT' in to['vincenzo']
    assert ctx.mailer.sent[0]['to'] == 'ceo@amalfi.test' and 'URGENT' in ctx.mailer.sent[0]['subject']
    align = db.execute("SELECT owner_id FROM agent_tasks WHERE dedupe_key LIKE %s ORDER BY dedupe_key",
                       (job['dedupe_key'] + ':align:%',)).fetchall()
    assert [a['owner_id'] for a in align] == ['vanessa', 'vincenzo']
    assert db.execute("SELECT 1 FROM agent_jobs WHERE kind='forecast_review'").fetchone()  # re-plan queued
    assert forecast.drift(ctx, _job('forecast_drift', job['dedupe_key'], job['payload'])).id == alert.id
    assert len(ctx.mailer.sent) == 1  # never alerts twice


def test_new_payroll_payee_and_big_new_cost_are_drift(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    fp.save(db, fp.Plan(Decimal('200000'), no_hiring=True, max_new_monthly_cost=Decimal('1000')), 'vincenzo')
    db.commit()
    watcher = forecast.DriftWatcher(conn_factory, ctx.jobs)
    watcher.poll()
    today = date.today()
    _txn(db, 'pay-old-1', today - timedelta(days=40), '-2500', 'payroll', 'kitchen', 'Rosa Pavese')
    _txn(db, 'pay-old-2', today - timedelta(days=1), '-2500', 'payroll', 'kitchen', 'Rosa Pavese')  # known payee
    _txn(db, 'pay-new', today, '-2400', 'payroll', 'front', 'New Waiter')
    _txn(db, 'saas-new', today, '-1800', 'it', 'it', 'Shiny SaaS')
    _txn(db, 'small-new', today, '-50', 'office', 'front', 'Pens Ltd')
    db.commit()
    assert watcher.poll() == 2
    jobs = {r['dedupe_key']: r['payload'] for r in db.execute("SELECT * FROM agent_jobs WHERE kind='forecast_drift'")}
    assert jobs == {'drift:txn:pay-new': {'rule': 'no_hiring', 'txn_id': 'pay-new'},
                    'drift:txn:saas-new': {'rule': 'max_new_monthly_cost', 'txn_id': 'saas-new'}}
    alert = forecast.drift(ctx, _job('forecast_drift', 'drift:txn:saas-new', jobs['drift:txn:saas-new']))
    assert 'Shiny SaaS' in alert.title and alert.owner_id == 'vincenzo'
    assert any(m['to'] == 'vincenzo' for m in ctx.notifier.sent)


def test_no_alerts_without_a_plan(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    watcher = forecast.DriftWatcher(conn_factory, ctx.jobs)
    ctx.recorder.open('web-hiring:x', 'liquidity', 'Hire a cook', requested_by='rosa')
    assert watcher.poll() == 0 and watcher.poll() == 0


# -- API ---------------------------------------------------------------------------------------------------------
def test_plan_api_is_owner_only_and_queues_a_review(conn_factory, db, tmp_path):
    make_world(conn_factory, tmp_path)
    _seed_year(db)
    with bind_tenant(OWNER):
        got = api.get_forecast_plan(db)
        assert got['plan'] is None and Decimal(got['defaults']['default_target']) > 0
        saved = api.put_forecast_plan(db, 'vincenzo', {'annual_revenue_target': '180000', 'no_hiring': True,
                                                       'max_new_monthly_cost': '', 'guidance': 'Grow online orders',
                                                       'enabled': True})
        assert saved['plan']['annual_revenue_target'] == '180000.00' and saved['queued']
        assert api.get_forecast_plan(db)['plan']['no_hiring'] is True
        assert api.forecast_review(db, 'vincenzo')['status'] == 'queued'
        with pytest.raises(ApiError):
            api.put_forecast_plan(db, 'vincenzo', {'annual_revenue_target': '0'})
    assert db.execute("SELECT count(*) AS n FROM agent_jobs WHERE kind='forecast_review'").fetchone()['n'] == 2
    with bind_tenant(STAFF):
        for call in (lambda: api.get_forecast_plan(db), lambda: api.forecast_review(db, 'vanessa'),
                     lambda: api.put_forecast_plan(db, 'vanessa', {'annual_revenue_target': '1'})):
            with pytest.raises(ApiError):
                call()


def test_forecast_constraint_upgrade_is_repeatable(db):
    from cfo.db.migrate import SCOUT_UPGRADE_PATHS
    db.execute("ALTER TABLE agent_tasks DROP CONSTRAINT agent_tasks_loop_check")
    db.execute("ALTER TABLE agent_tasks ADD CONSTRAINT agent_tasks_loop_check CHECK(loop IN "
               "('invoicing','cost_control','liquidity','chat','expenses','strategy'))")
    db.execute("DROP TABLE forecast_plan")
    for path in SCOUT_UPGRADE_PATHS:
        db.execute(path.read_text())
        db.execute(path.read_text())
    db.execute("INSERT INTO agent_tasks(dedupe_key,loop,title) VALUES ('forecast:upgrade','forecast','Plan')")
    db.execute("INSERT INTO forecast_plan(annual_revenue_target) VALUES (1)")
    db.commit()
