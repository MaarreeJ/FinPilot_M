from datetime import date, timedelta
import pytest
from fastapi.testclient import TestClient
from app.agents import Analyzer, Categorizer, Forecaster
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('FINPILOT_DB', str(tmp_path / 'test.db'))
    monkeypatch.delenv('MESH_API_KEY', raising=False)
    monkeypatch.delenv('MESH_MODEL', raising=False)
    monkeypatch.delenv('FINPILOT_TOKEN', raising=False)
    with TestClient(app) as c:
        yield c


def test_demo_end_to_end(client):
    assert client.get('/').status_code == 200
    assert client.post('/api/demo').status_code == 200
    report = client.get('/api/dashboard').json()
    pattern = next(p for p in report['analysis']['patterns'] if p['category'] == 'food_delivery')
    assert pattern['increase_pct'] == 40
    assert report['balance'] == 45000
    assert report['forecast']['observed_days'] == 90
    assert report['recommendations'][0]['potential_saving'] == 1600
    result = client.post('/api/simulate', json={'cost': 100000, 'days': 30, 'reserve': 10000}).json()
    assert not result['affordable_under_model']
    assert result['after_purchase'] == round(result['baseline'] - 100000, 2)
    assert client.get('/api/audit').json()[0]['report']['recommendations']
    assert client.post('/api/demo').status_code == 409


def test_csv_atomic_validation_and_deduplication(client):
    today = date.today().isoformat()
    invalid = f'date,merchant,amount\n{today},Uber,-100\nwrong,Salary,1000\n'
    assert client.post('/api/import', files={'file': ('bad.csv', invalid)}).status_code == 422
    assert client.get('/api/transactions').json() == []
    valid = f'date,merchant,amount\n{today},Uber,-100\n'
    assert client.post('/api/import', files={'file': ('ok.csv', valid)}).json()['imported'] == 1
    assert client.post('/api/import', files={'file': ('ok.csv', valid)}).json()['duplicates_skipped'] == 1


def test_invalid_inputs_and_empty_forecast(client):
    assert client.post('/api/simulate', json={'cost': 10}).status_code == 422
    for merchant, day in [(' ', date.today()), ('Uber', date.today() + timedelta(days=1))]:
        assert client.post('/api/transactions', json={'date': str(day), 'merchant': merchant, 'amount': -1}).status_code == 422
    assert client.post('/api/simulate', json={'cost': -20}).status_code == 422
    assert client.post('/api/chat', json={'question': 'What changed?'}).status_code == 503


def test_token_auth(client, monkeypatch):
    monkeypatch.setenv('FINPILOT_TOKEN', 'test-token')
    assert client.get('/api/dashboard').status_code == 401
    assert client.post('/api/demo').status_code == 401
    assert client.get('/api/dashboard', headers={'Authorization': 'Bearer test-token'}).status_code == 200


def test_window_boundaries_and_forecast_no_future_leakage():
    today = date(2026, 10, 18)
    rows = [{'date': str(today - timedelta(days=age)), 'category': 'shopping', 'amount': -amount}
            for age, amount in [(0, 150), (29, 150), (30, 100), (59, 100), (60, 9000)]]
    pattern = Analyzer().run(rows, today)['patterns'][0]
    assert pattern['current'] == 300 and pattern['previous'] == 200
    forecast = Forecaster().run(rows, 10000, today)
    rows.append({'date': str(today + timedelta(days=1)), 'amount': 1e6})
    assert Forecaster().run(rows, 10000, today) == forecast


def test_mesh_routing_and_grounding(client, monkeypatch):
    from types import SimpleNamespace
    import openai
    observed = {}
    class FakeOpenAI:
        def __init__(self, **kwargs):
            observed.update(kwargs)
            self.chat = SimpleNamespace(completions=self)
        def create(self, **kwargs):
            observed['request'] = kwargs
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='Grounded answer'))])
    monkeypatch.setenv('MESH_API_KEY', 'test-only-key')
    monkeypatch.setattr(openai, 'OpenAI', FakeOpenAI)
    assert client.post('/api/chat', json={'question': 'Explain my patterns'}).json()['answer'] == 'Grounded answer'
    assert observed['base_url'] == 'https://api.meshapi.ai/v1'
    assert observed['api_key'] == 'test-only-key'
    assert observed['request']['model'] == 'openai/gpt-4o'
    assert 'evidence' in observed['request']['messages'][1]['content']
    monkeypatch.setenv('MESH_MODEL', 'custom-test-model')
    response = client.post('/api/chat', json={'question': 'Explain my patterns'})
    assert response.json()['provider'] == 'Mesh API'
    assert observed['request']['model'] == 'custom-test-model'


def test_mesh_configuration_and_missing_key(client, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'unused-direct-key')
    assert client.get('/health').json()['mesh_configured'] is False
    response = client.post('/api/chat', json={'question': 'What changed?'})
    assert response.status_code == 503
    assert 'MESH_API_KEY' in response.json()['detail']
    monkeypatch.setenv('MESH_API_KEY', '   ')
    assert client.get('/health').json()['mesh_configured'] is False
    monkeypatch.setenv('MESH_API_KEY', 'test-only-key')
    assert client.get('/health').json()['mesh_configured'] is True


def test_scheduler_registered(client):
    assert app.state.scheduler.running
    assert app.state.scheduler.get_job('daily_digest') is not None
    assert client.post('/api/digest').status_code == 200
    assert len(client.get('/api/audit').json()) == 1


def test_savings_change_scenario_by_expected_amount(client):
    client.post('/api/demo')
    first = client.post('/api/simulate', json={'cost': 5000, 'days': 60}).json()
    second = client.post('/api/simulate', json={'cost': 5000, 'days': 60, 'monthly_saving': 2000}).json()
    assert second['after_purchase'] - first['after_purchase'] == pytest.approx(4000)


def test_stale_history_not_presented_as_current_forecast():
    result = Forecaster().run([{'date': '2020-01-01', 'amount': 10000}], 2000, date(2026, 10, 18))
    assert result['available'] is False


def test_mesh_spending_limit_has_actionable_message(client, monkeypatch):
    import httpx
    import openai
    def unavailable(*args):
        response = httpx.Response(402, request=httpx.Request('POST', 'https://api.meshapi.ai/v1/chat/completions'))
        raise openai.APIStatusError('Limit reached', response=response, body={'code': 'spend_limit_exceeded'})
    monkeypatch.setattr('app.main.converse', unavailable)
    response = client.post('/api/chat', json={'question': 'Explain my spending'})
    assert response.status_code == 503
    assert 'spending limit' in response.json()['detail']
    assert 'sponsor credits' in response.json()['detail']
