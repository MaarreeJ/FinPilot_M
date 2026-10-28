"""Check graph semantics, concurrent request isolation, and proposal-only behavior."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agents import Advisor, Analyzer, Coordinator, Forecaster
from app.main import app
from app.sample import sample_transactions
from app.agents import Categorizer


@pytest.fixture
def snapshot():
    today = date(2026, 10, 27)
    rows = sample_transactions(today)
    for row in rows:
        row['category'] = Categorizer().run(row)
    return rows, today


def test_graph_matches_financial_tools(snapshot):
    rows, today = snapshot
    before = deepcopy(rows)
    report = Coordinator().run(rows, 45000, today)
    analysis = Analyzer().run(rows, today)
    forecast = Forecaster().run(rows, 45000, today)
    assert report['analysis'] == analysis
    assert report['forecast'] == forecast
    assert report['recommendations'] == Advisor().run(analysis, forecast)
    assert report['execution_trace'] == ['analyze', 'forecast', 'advise', 'assemble']
    assert report['orchestration'] == 'langgraph'
    assert rows == before
    assert all(r['trigger'] and r['evidence'] for r in report['recommendations'])


def test_scenario_branch_uses_requested_horizon(snapshot):
    rows, today = snapshot
    scenario = {'cost': 100000, 'days': 60, 'reserve': 15000, 'monthly_saving': 3000}
    report = Coordinator().run(rows, 45000, today, scenario)
    assert report['execution_trace'] == ['analyze', 'forecast', 'simulate', 'advise', 'assemble']
    assert report['forecast']['horizon_days'] == 60
    assert report['simulation']['after_purchase'] == pytest.approx(
        report['forecast']['projected_balance'] - 100000 + 6000)
    assert not report['simulation']['affordable_under_model']
    assert 'simulation' not in Coordinator().run(rows, 45000, today)


def test_parallel_requests_do_not_share_financial_state(snapshot):
    rows, today = snapshot
    coordinator = Coordinator()
    balances = [1000, 9000, 45000, 100000]
    with ThreadPoolExecutor(max_workers=4) as pool:
        reports = list(pool.map(lambda balance: coordinator.run(rows, balance, today), balances))
    assert [r['balance'] for r in reports] == balances
    for report, balance in zip(reports, balances):
        assert report['forecast'] == Forecaster().run(rows, balance, today)
        assert report['execution_trace'] == ['analyze', 'forecast', 'advise', 'assemble']


def test_no_history_branch_returns_unavailable():
    report = Coordinator().run([], 1000, date(2026, 10, 27))
    assert report['forecast']['available'] is False
    assert report['recommendations'] == []
    with pytest.raises(ValueError, match='Import transactions'):
        Coordinator().run([], 1000, date(2026, 10, 27),
                          {'cost': 100, 'days': 30, 'reserve': 0, 'monthly_saving': 0})


def test_advisor_uses_actual_horizon():
    advice = Advisor().run({'patterns': []},
                          {'available': True, 'projected_balance': -50,
                           'observed_days': 90, 'horizon_days': 60})
    assert 'day 60' in advice[0]['action']


def test_graph_proposals_require_explicit_save(tmp_path, monkeypatch):
    monkeypatch.setenv('FINPILOT_DB', str(tmp_path / 'graph.db'))
    monkeypatch.delenv('FINPILOT_TOKEN', raising=False)
    with TestClient(app) as client:
        client.post('/api/demo')
        before_history = client.get('/api/audit').json()
        before_rows = client.get('/api/transactions').json()
        report = client.get('/api/dashboard').json()
        simulation = client.post('/api/simulate', json={'cost': 5000}).json()
        assert simulation['orchestration'] == 'langgraph'
        assert 'simulate' in simulation['execution_trace']
        assert client.get('/api/audit').json() == before_history
        assert client.get('/api/transactions').json() == before_rows
        assert client.get('/api/dashboard').json()['balance'] == report['balance']
        client.post('/api/digest')
        saved = client.get('/api/audit').json()[0]['report']
        assert saved['execution_trace'] == report['execution_trace']
        assert saved['recommendations'] == report['recommendations']


def test_recorded_evaluation_is_reproducible():
    from evaluation.run import evaluate
    expected = json.loads((Path(__file__).resolve().parents[1] / 'evaluation/results.json').read_text())
    assert evaluate() == expected
