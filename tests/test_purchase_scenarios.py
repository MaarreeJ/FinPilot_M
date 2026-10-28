
from datetime import date, timedelta
import pytest
from fastapi.testclient import TestClient
from app import main

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('FINPILOT_DB', str(tmp_path / 'scenarios.db'))
    monkeypatch.delenv('FINPILOT_TOKEN', raising=False)
    with TestClient(main.app) as c:
        yield c

def payload(**changes):
    result = {'name': 'Goa trip', 'category': 'travel',
              'planned_date': str(date.today() + timedelta(days=18)),
              'cost': 12000, 'reserve': 10000, 'monthly_saving': 0}
    result.update(changes)
    return result

def test_save_list_delete_purchase(client):
    client.post('/api/demo')
    response = client.post('/api/purchase-scenarios', json=payload())
    assert response.status_code == 201, response.text
    data = response.json()
    assert data['name'] == 'Goa trip'
    assert data['category'] == 'travel'
    assert data['simulation']['after_purchase'] == data['simulation']['baseline'] - 12000
    assert data['evidence']['transaction_ids']
    assert client.get('/api/purchase-scenarios').json()[0]['id'] == data['id']
    assert client.delete(f"/api/purchase-scenarios/{data['id']}").status_code == 200
    assert client.get('/api/purchase-scenarios').json() == []

def test_invalid_purchase(client):
    assert client.post('/api/purchase-scenarios', json=payload(name='   ')).status_code == 422
    assert client.post('/api/purchase-scenarios', json=payload(planned_date=str(date.today()))).status_code == 422
    assert client.post('/api/purchase-scenarios', json=payload(cost=-100)).status_code == 422
    assert client.delete('/api/purchase-scenarios/999999').status_code == 404

def test_saved_scenario_persists(client):
    client.post('/api/demo')
    response=client.post('/api/purchase-scenarios', json=payload())
    assert response.status_code == 201
    with main.connect() as db:
        count=db.execute('SELECT COUNT(*) FROM purchase_scenarios').fetchone()[0]
    assert count == 1
