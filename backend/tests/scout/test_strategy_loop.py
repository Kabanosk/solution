from datetime import date
from decimal import Decimal

import pytest

from cfo.api.errors import ApiError
from cfo.tenancy import LEGACY_ID, LEGACY_SLUG, Tenant, bind_tenant
from scout import api
from scout.loops import strategy
from scout.models import Job
from test_invoicing_loop import make_world


def test_strategy_persists_once_and_is_owner_only(conn_factory, db, tmp_path, monkeypatch):
    ctx = make_world(conn_factory, tmp_path)
    monkeypatch.setattr(strategy, 'fetch_news', lambda *args: {'status':'ok','sources':[], 'items':[]})
    for m, amount in [(7,900),(8,1000),(9,1100),(10,1210)]:
        db.execute("INSERT INTO transactions(id,booking_date,type,reference,counterparty,amount,currency,category) "
                   "VALUES (%s,%s,'settlement','','customers',%s,'EUR','revenue')", (f'rev-{m}',date(2026,m,15),Decimal(amount)))
    db.commit()
    job = Job(id=1,kind='strategy_review',payload={'day':'2026-11-21'},dedupe_key='strategy:2026-11-21',
              status='running',attempts=1,run_after=ctx.now())
    task = strategy.run(ctx,job)
    assert task.loop == 'strategy' and task.status == 'done'
    assert task.evidence[0]['kind'] == 'strategy_briefing'
    assert strategy.run(ctx,job).id == task.id
    assert db.execute("SELECT count(*) AS n FROM agent_tasks WHERE loop='strategy'").fetchone()['n'] == 1
    owner = Tenant(id=LEGACY_ID,slug=LEGACY_SLUG,schema_name='public',employee_id='vincenzo',role='owner',legacy=True)
    staff = Tenant(id=LEGACY_ID,slug=LEGACY_SLUG,schema_name='public',employee_id='vanessa',role='member',legacy=True)
    with bind_tenant(owner):
        assert api.get_task(task.id,db)['task']['id'] == task.id
        assert len(api.list_tasks(db,loop='strategy')['items']) == 1
    with bind_tenant(staff):
        assert api.list_tasks(db,loop='strategy')['items'] == []
        with pytest.raises(ApiError): api.get_task(task.id,db)
        with pytest.raises(ApiError): api.strategy_review(db,'vanessa')
    assert not ctx.notifier.sent


def test_strategy_constraint_upgrade_is_repeatable(db):
    from cfo.db.migrate import SCOUT_UPGRADE_PATHS
    db.execute("ALTER TABLE agent_tasks DROP CONSTRAINT agent_tasks_loop_check")
    db.execute("ALTER TABLE agent_tasks ADD CONSTRAINT agent_tasks_loop_check CHECK(loop IN ('invoicing','cost_control','liquidity','chat','expenses'))")
    migration = next(path for path in SCOUT_UPGRADE_PATHS if path.name == 'scout_strategy.sql')
    db.execute(migration.read_text())
    db.execute(migration.read_text())
    db.execute("INSERT INTO agent_tasks(dedupe_key,loop,title) VALUES ('strategy:upgrade','strategy','Briefing')")
    db.commit()
