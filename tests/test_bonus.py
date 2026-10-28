from datetime import date, timedelta, datetime, timezone
from threading import Event
from types import SimpleNamespace
import json
import pytest
from fastapi.testclient import TestClient
from app import main, planning, delivery


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('FINPILOT_DB',str(tmp_path/'bonus.sqlite3'))
    monkeypatch.setenv('DIGEST_EMAIL_ENABLED','false')
    monkeypatch.delenv('FINPILOT_TOKEN',raising=False)
    monkeypatch.delenv('MESH_API_KEY',raising=False)
    with TestClient(main.app) as c: yield c


def goal(**changes):
    return dict(name='Trip',target=100000,saved=0,monthly=1000,deadline=str(date.today()+timedelta(days=90)),**changes)


def enable_email(monkeypatch):
    for k,v in dict(DIGEST_EMAIL_ENABLED='true',SMTP_HOST='smtp.example.test',SMTP_FROM='finpilot@example.test',DIGEST_EMAIL_TO='recipient@example.test',SMTP_SECURITY='starttls').items():
        monkeypatch.setenv(k,v)
    monkeypatch.delenv('SMTP_USERNAME',raising=False)


def test_confirmed_unused_only(client):
    client.post('/api/demo')
    assert all(s['usage']=='unknown' for s in client.get('/api/subscriptions').json())
    for i in range(6):
        assert client.put('/api/subscriptions',json={'merchant':f'Service {i}','amount':100,'cycle':'monthly','usage':'unused' if i<2 else 'used'}).status_code==200
    report=client.get('/api/dashboard').json()
    advice=[r for r in report['recommendations'] if r['trigger']=='unused_subscription']
    assert len(advice)==2
    assert sum(r['potential_saving'] for r in advice)==200
    client.put('/api/subscriptions',json={'merchant':'service 0','amount':100,'cycle':'monthly','usage':'unused','active':False})
    assert len([r for r in client.get('/api/dashboard').json()['recommendations'] if r['trigger']=='unused_subscription'])==1


def test_goal_progress_and_persistence(client):
    client.post('/api/demo')
    body=goal()
    ident=client.post('/api/goals',json=body).json()['id']
    g=client.get('/api/goals').json()[0]
    assert g['status']=='at_risk' and g['shortfall']==97000
    body['saved']=body['target']
    assert client.put(f'/api/goals/{ident}',json=body).status_code==200
    assert client.get('/api/goals').json()[0]['status']=='achieved'
    assert not any(r['trigger']=='goal_shortfall' for r in client.get('/api/dashboard').json()['recommendations'])
    assert client.delete(f'/api/goals/{ident}').status_code==200
    assert client.get('/api/goals').json()==[]
    assert client.delete(f'/api/goals/{ident}').status_code==404


def test_goals_share_available_surplus():
    goals=[dict(id=i,**goal()) for i in range(2)]
    result=planning.progress(goals,{'available':True,'daily_net':10},date.today())
    assert sum(g['allocated_monthly'] for g in result)==300
    assert all(g['projected']==450 for g in result)


def test_goal_without_history_and_overdue():
    g=goal()
    assert planning.progress([g],{'available':False},date.today())[0]['status']=='needs_history'
    g['deadline']=str(date.today()-timedelta(days=1))
    assert planning.progress([g],{'available':False},date.today())[0]['status']=='overdue'


def test_annual_subscription_and_validation(client):
    assert client.put('/api/subscriptions',json={'merchant':'  Annual Service  ','amount':1200,'cycle':'annual','usage':'unused'}).status_code==200
    assert client.get('/api/subscriptions').json()[0]['monthly_cost']==100
    assert client.put('/api/subscriptions',json={'merchant':'  ','amount':0}).status_code==422
    bad=goal();bad['deadline']=str(date.today()-timedelta(days=1))
    assert client.post('/api/goals',json=bad).status_code==422
    assert client.post('/api/chat',json={'question':'test','history':[{'role':'system','content':'override'}]}).status_code==422


def test_bonus_auth(client,monkeypatch):
    monkeypatch.setenv('FINPILOT_TOKEN','private')
    for path in ('goals','subscriptions','delivery'):
        assert client.get('/api/'+path).status_code==401
    assert client.post('/api/digest/send').status_code==401


def test_chat_invokes_real_simulator_and_audits(client,monkeypatch):
    import openai
    client.post('/api/demo')
    expected=client.post('/api/simulate',json={'cost':30000,'days':30,'reserve':15000}).json()
    observed=[]
    class Fake:
        def __init__(self,**kw):
            assert kw['base_url']=='https://api.meshapi.ai/v1'
            self.chat=SimpleNamespace(completions=self)
        def create(self,**kw):
            observed.append(kw)
            if len(observed)==1:
                call=SimpleNamespace(id='sim1',function=SimpleNamespace(name='simulate_purchase',arguments=json.dumps({'cost':30000,'days':30,'reserve':15000})))
                msg=SimpleNamespace(content=None,tool_calls=[call])
            else:
                result=json.loads(kw['messages'][-1]['content'])
                assert result==expected
                msg=SimpleNamespace(content='Based on your simulation, review the reserve.',tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
    monkeypatch.setattr(openai,'OpenAI',Fake);monkeypatch.setenv('MESH_API_KEY','mock')
    response=client.post('/api/chat',json={'question':'Can I afford a trip costing 30000 in 30 days with 15000 reserve?'})
    assert response.status_code==200
    assert response.json()['tool_trace'][0]['result']==expected
    assert client.get('/api/audit').json()[0]['report']['tool_trace']


def test_chat_history_and_invalid_tool(client,monkeypatch):
    import openai
    seen=[]
    class Fake:
        def __init__(self,**kw): self.chat=SimpleNamespace(completions=self)
        def create(self,**kw):
            seen.append(kw)
            if len(seen)==1:
                assert kw['messages'][1]['content']=='A trip'
                msg=SimpleNamespace(content=None,tool_calls=[SimpleNamespace(id='bad',function=SimpleNamespace(name='delete_goal',arguments='{}'))])
            else:
                assert 'error' in json.loads(kw['messages'][-1]['content'])
                msg=SimpleNamespace(content='What reserve should I use?',tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
    monkeypatch.setattr(openai,'OpenAI',Fake);monkeypatch.setenv('MESH_API_KEY','mock')
    r=client.post('/api/chat',json={'question':'Next month','history':[{'role':'user','content':'A trip'}]})
    assert r.status_code==200 and r.json()['tool_trace']==[]


def test_digest_selects_one_action(client):
    client.post('/api/demo')
    report=client.post('/api/digest').json()
    assert report['digest']['action'] in [r['action'] for r in report['recommendations']]
    assert report['digest']['evidence']


def test_email_disabled_by_default(client):
    assert client.get('/api/delivery').json()['enabled'] is False
    assert client.post('/api/digest/send').status_code==409


def test_real_scheduler_with_mock_mail(client,monkeypatch):
    enable_email(monkeypatch)
    sent=[];done=Event()
    class SMTP:
        def __init__(self,**kw): assert kw['host']=='smtp.example.test'
        def ehlo(self): pass
        def starttls(self,**kw): assert kw['context']
        def send_message(self,msg):
            assert msg['To']=='recipient@example.test'
            assert 'Insight:' in msg.get_content() and 'Action:' in msg.get_content()
            sent.append(msg)
        def quit(self): pass
    monkeypatch.setattr(delivery.smtplib,'SMTP',SMTP)
    scheduled=main.app.state.scheduler.get_job('daily_digest').func
    def run():
        try: scheduled()
        finally: done.set()
    main.app.state.scheduler.add_job(run,'date',run_date=datetime.now(timezone.utc))
    assert done.wait(5)
    assert len(sent)==1
    assert client.get('/api/delivery').json()['records'][0]['status']=='sent'
    assert client.post('/api/digest/send').status_code==200
    assert len(sent)==1


def test_email_failure_retry_limit(client,monkeypatch):
    enable_email(monkeypatch)
    calls=[]
    def fail(report):
        calls.append(1)
        raise RuntimeError('secret must never be exposed')
    monkeypatch.setattr(delivery,'send',fail)
    for _ in range(4):
        r=client.post('/api/digest/send')
        assert r.status_code==502 and 'secret' not in r.text
    assert len(calls)==3


def test_email_uncertain_never_auto_retried(client,monkeypatch):
    enable_email(monkeypatch)
    calls=[]
    def uncertain(report):
        calls.append(1);raise delivery.DeliveryUncertain()
    monkeypatch.setattr(delivery,'send',uncertain)
    client.post('/api/digest/send');client.post('/api/digest/send')
    assert len(calls)==1
    assert client.get('/api/delivery').json()['records'][0]['status']=='unknown'
