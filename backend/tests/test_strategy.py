from datetime import date, datetime, timezone
from decimal import Decimal

import httpx

from scout.strategy import fetch_news, growth_scenarios, parse_feed

NOW = datetime(2026, 11, 21, tzinfo=timezone.utc)


def ledger():
    return [{"booking_date": date(2026, m, 15), "amount": Decimal(amount), "currency": "EUR", "category": "revenue"}
            for m, amount in [(7, '900'), (8, '1000'), (9, '1100'), (10, '1210'), (11, '40000')]]


def test_growth_excludes_partial_months_and_financing_and_currencies():
    rows = ledger() + [{"booking_date": date(2026, 10, 15), "amount": Decimal('999999'), "currency": c, "category": k}
                       for c, k in [('CHF', 'revenue'), ('EUR', 'loan')]]
    result = growth_scenarios(rows, NOW.date())
    assert result['status'] == 'scenario'
    assert len(result['monthly_revenue']) == 3
    assert result['observed_growth_pct'] == '10.00'
    assert result['scenarios'][1]['months'][0] == {'month': '2026-11-01', 'revenue': '1331.00'}
    assert result['scenarios'][1]['months'][-1]['revenue'] == '1610.51'
    assert any('Other currencies' in x for x in result['limitations'])


def test_growth_stale_gaps_zero_and_short_history_do_not_predict():
    assert growth_scenarios(ledger(), date(2027, 1, 1))['status'] == 'stale_data'
    assert growth_scenarios(ledger()[1:], NOW.date())['status'] == 'insufficient_data'
    rows = ledger()
    rows[2]['amount'] = 0
    assert growth_scenarios(rows, NOW.date())['scenarios'] == []
    assert growth_scenarios([], NOW.date())['status'] == 'insufficient_data'


def feed(items):
    return ('<rss><channel>' + ''.join(f'<item><title>{title}</title><link>{url}</link><pubDate>{day}</pubDate></item>'
        for title, url, day in items) + '</channel></rss>').encode()


def test_news_filters_stale_future_missing_date_irrelevant_and_unsafe_links():
    raw = feed([('Food prices', 'https://example.com/1', 'Fri, 20 Nov 2026 10:00:00 GMT'),
                ('Food prices', 'https://example.com/1', 'Fri, 20 Nov 2026 10:00:00 GMT'),
                ('Food prices', 'https://example.com/2', 'Fri, 20 Nov 2027 10:00:00 GMT'),
                ('Food prices', 'https://example.com/3', 'Fri, 20 Oct 2026 10:00:00 GMT'),
                ('Sports', 'https://example.com/4', 'Fri, 20 Nov 2026 10:00:00 GMT'),
                ('Food prices', 'javascript:alert(1)', 'Fri, 20 Nov 2026 10:00:00 GMT'),
                ('Food prices', 'https://example.com/5', '')])
    items = parse_feed(raw, 'test', NOW, {'food'})
    assert len(items) == 1
    assert 'Food purchasing' in items[0]['relevance']
    assert parse_feed(raw, 'test', NOW, {'payroll'}) == []


def test_news_atom_and_partial_source_failure():
    xml = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Food prices</title><link href="https://example.com/news"/><updated>2026-11-20T10:00:00Z</updated></entry></feed>'
    assert parse_feed(xml, 'test', NOW, {'food'})[0]['url'] == 'https://example.com/news'
    def handler(request):
        return httpx.Response(200, content=xml) if request.url.host == 'www.ecb.europa.eu' else httpx.Response(503)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = fetch_news(NOW, {'food'}, client)
    assert result['status'] == 'partial'
    assert len(result['items']) == 1
    assert result['sources'][1]['status'] == 'unavailable'


def test_unavailable_is_different_from_no_matching_news():
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
        assert fetch_news(NOW, {'food'}, client)['status'] == 'unavailable'
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b'<rss><channel/></rss>'))) as client:
        result = fetch_news(NOW, {'food'}, client)
        assert result['status'] == 'ok' and result['items'] == []


def test_worker_scheduler_keeps_cost_dedupe_and_leaves_strategy_to_dashboard():
    from scout.watchers import Scheduler
    class Queue:
        def __init__(self): self.keys = set()
        def enqueue(self, kind, payload, key):
            if key in self.keys: return None
            self.keys.add(key)
            return len(self.keys)
    queue = Queue()
    scheduler = Scheduler(queue)
    assert scheduler.tick(datetime(2026, 11, 21, 5, 59, tzinfo=timezone.utc)) == []
    assert scheduler.tick(datetime(2026, 11, 21, 6, 0, tzinfo=timezone.utc)) == ['cost_check', 'forecast_review']
    assert scheduler.tick(datetime(2026, 11, 21, 7, 0, tzinfo=timezone.utc)) == []
    assert 'strategy:2026-11-21' not in queue.keys


def test_daily_note_retry_is_idempotent_and_does_not_send(monkeypatch):
    from types import SimpleNamespace
    from scout.config import ScoutSettings
    from scout.loops import strategy
    class Recorder:
        task = None
        def by_key(self, key): return self.task
        def open(self, key, loop, title, **kwargs):
            self.task = SimpleNamespace(id='SC-1', status='in_progress', loop=loop)
            return self.task
        def step(self, *args, **kwargs): pass
        def update(self, task_id, **kwargs): self.evidence = kwargs['evidence']
        def set_status(self, task_id, status, **kwargs):
            self.task.status = status
            self.task.outcome = kwargs['outcome']
            return self.task
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, *args): return self
        def fetchall(self): return ledger()
    recorder = Recorder()
    calls = []
    def news(*args):
        calls.append(1)
        return {'status':'unavailable', 'sources':[], 'items':[]}
    monkeypatch.setattr(strategy, 'fetch_news', news)
    ctx = SimpleNamespace(now=lambda:NOW, recorder=recorder, conn_factory=Conn,
                          settings=ScoutSettings(_env_file=None, strategy_monthly_revenue_target=1500))
    job = SimpleNamespace(payload={'day':'2026-11-21'})
    first = strategy.run(ctx, job)
    assert 'Sales target: below target' in first.outcome
    assert 'incomplete' in first.outcome
    assert recorder.evidence[0]['scorecard']['expansion']['status'] == 'unknown'
    assert strategy.run(ctx, job) is first
    assert len(calls) == 1


def test_tenant_settings_never_inherit_legacy_targets(monkeypatch):
    from scout.config import ScoutSettings, tenant_settings
    from cfo.tenancy import Tenant, bind_tenant
    tenant = Tenant(id='12345678-1234-4234-8234-123456789abc', slug='other',
                    schema_name='tenant_12345678123442348234123456789abc', employee_id='owner', role='owner')
    with bind_tenant(tenant):
        s = tenant_settings(ScoutSettings(_env_file=None, strategy_monthly_revenue_target=100,
                                         strategy_operating_surplus_target=50, strategy_owner_income_target=10))
    assert s.strategy_monthly_revenue_target is None
    assert s.strategy_operating_surplus_target is None
    assert s.strategy_owner_income_target is None


def test_employment_gender_gap_is_not_a_payroll_cost_signal():
    raw = feed([('Average gender employment gap in EU regions at 9.7 pp', 'https://example.com/gap', 'Fri, 20 Nov 2026 10:00:00 GMT')])
    assert parse_feed(raw, 'test', NOW, {'payroll'}) == []


def test_news_rejects_dtd_and_oversized_payloads():
    import pytest
    from scout.strategy import MAX_BYTES
    with pytest.raises(ValueError):
        parse_feed(b'<!DOCTYPE rss [<!ENTITY payload "boom">]><rss/>', 'test', NOW, {'food'})
    with pytest.raises(ValueError):
        parse_feed(b'x' * (MAX_BYTES + 1), 'test', NOW, {'food'})
