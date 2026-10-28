from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal
import csv
import io
import json
import os
import secrets
import sqlite3

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Header
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from . import planning, delivery
from .agents import Categorizer, Coordinator, converse, money

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


@contextmanager
def connect():
    db = sqlite3.connect(os.getenv('FINPILOT_DB', 'finpilot.db'), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY, date TEXT, merchant TEXT, amount REAL, category TEXT, UNIQUE(date, merchant, amount))')
    db.execute('CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY, created_at TEXT, payload TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), balance REAL DEFAULT 0)')
    db.execute('INSERT OR IGNORE INTO settings(id,balance) VALUES(1,0)')
    db.execute('CREATE TABLE IF NOT EXISTS subscriptions (merchant TEXT PRIMARY KEY COLLATE NOCASE, payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS goals (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS deliveries (day TEXT PRIMARY KEY, status TEXT, attempts INTEGER, payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS purchase_scenarios (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)')
    db.commit()
    try:
        with db:
            yield db
    finally:
        db.close()


def dashboard():
    with connect() as db:
        rows = [dict(row) for row in db.execute('SELECT * FROM transactions ORDER BY date, id')]
        balance = db.execute('SELECT balance FROM settings WHERE id=1').fetchone()[0]
        subscriptions = [json.loads(r['payload']) for r in db.execute('SELECT payload FROM subscriptions')]
        goals = [dict(id=r['id'], **json.loads(r['payload'])) for r in db.execute('SELECT * FROM goals')]
    report = Coordinator().run(rows, balance, date.today(), subscriptions=subscriptions, goals=goals)
    report['transaction_ids'] = [row['id'] for row in rows]
    return report


def save_report(report):
    with connect() as db:
        db.execute('INSERT INTO reports(created_at,payload) VALUES(?,?)',
                   (datetime.now(timezone.utc).isoformat(), json.dumps(report)))
    return report


def save_digest():
    report = dashboard()
    report['digest'] = planning.digest(report)
    return save_report(report)


@asynccontextmanager
async def lifespan(app):
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler(timezone='UTC')
    scheduler.add_job(scheduled_digest, 'cron', hour=int(os.getenv('DIGEST_HOUR_UTC', '3')),
                      id='daily_digest', max_instances=1, coalesce=True)
    app.state.scheduler = scheduler
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title='FinPilot', version='0.1.0', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=Path(__file__).parent / 'static'), name='static')


def authorize(authorization: str = Header(default='')):
    token = os.getenv('FINPILOT_TOKEN', '')
    if token and not secrets.compare_digest(authorization, f'Bearer {token}'):
        raise HTTPException(401, 'Valid bearer token required')


class Transaction(BaseModel):
    date: date
    merchant: str = Field(min_length=1, max_length=200)
    amount: float = Field(allow_inf_nan=False, ge=-1e9, le=1e9)

    @field_validator('merchant')
    @classmethod
    def clean_merchant(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Merchant must not be blank')
        return value

    @field_validator('date')
    @classmethod
    def past_or_today(cls, value):
        if value > date.today():
            raise ValueError('Use the simulator for future transactions')
        return value


class Balance(BaseModel):
    balance: float = Field(allow_inf_nan=False, ge=-1e12, le=1e12)


class Scenario(BaseModel):
    cost: float = Field(gt=0, le=1e9, allow_inf_nan=False)
    days: int = Field(default=30, ge=1, le=365)
    reserve: float = Field(default=0, ge=0, le=1e12, allow_inf_nan=False)
    monthly_saving: float = Field(default=0, ge=0, le=1e9, allow_inf_nan=False)


class NamedPurchase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    category: Literal['travel', 'electronics', 'shopping', 'education', 'health', 'home', 'other'] = 'other'
    planned_date: date
    cost: float = Field(gt=0, le=1e9, allow_inf_nan=False)
    reserve: float = Field(default=0, ge=0, le=1e12, allow_inf_nan=False)
    monthly_saving: float = Field(default=0, ge=0, le=1e9, allow_inf_nan=False)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        value = ' '.join(value.split())
        if not value:
            raise ValueError('Purchase name required')
        return value

    @field_validator('planned_date')
    @classmethod
    def valid_plan_date(cls, value):
        days = (value - date.today()).days
        if not 1 <= days <= 365:
            raise ValueError('Planned date must be 1–365 days in the future')
        return value


class ChatMessage(BaseModel):
    role: Literal['user', 'assistant']
    content: str = Field(min_length=1, max_length=6000)


class Question(BaseModel):
    history: list[ChatMessage] = Field(default_factory=list, max_length=12)
    question: str = Field(min_length=1, max_length=2000)


def insert(db, t):
    row = t.model_dump(mode='json')
    row['amount'] = money(row['amount'])
    category = Categorizer().run(row)
    cursor = db.execute('INSERT OR IGNORE INTO transactions(date,merchant,amount,category) VALUES(?,?,?,?)',
                        (row['date'], row['merchant'].strip(), row['amount'], category))
    return cursor.rowcount


@app.get('/')
def home():
    return FileResponse(Path(__file__).parent / 'static' / 'index.html')


@app.get('/health')
def health():
    return {'status': 'ok', 'mesh_configured': bool(os.getenv('MESH_API_KEY', '').strip())}


@app.get('/api/dashboard', dependencies=[Depends(authorize)])
def get_dashboard():
    return dashboard()


@app.post('/api/balance', dependencies=[Depends(authorize)])
def set_balance(body: Balance):
    with connect() as db:
        db.execute('UPDATE settings SET balance=? WHERE id=1', (body.balance,))
    return {'balance': body.balance}


@app.post('/api/transactions', dependencies=[Depends(authorize)])
def add_transaction(body: Transaction):
    with connect() as db:
        count = insert(db, body)
    return {'imported': count}


@app.post('/api/import', dependencies=[Depends(authorize)])
async def import_csv(file: UploadFile = File(...)):
    content = await file.read(2_000_001)
    if len(content) > 2_000_000:
        raise HTTPException(413, 'CSV limit is 2 MB')
    try:
        reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
        if not {'date', 'merchant', 'amount'}.issubset(reader.fieldnames or []):
            raise ValueError('Required columns: date,merchant,amount')
        rows = [Transaction.model_validate(row) for row in reader]
        if not rows:
            raise ValueError('CSV is empty')
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(422, 'Invalid CSV: use date YYYY-MM-DD, merchant, finite signed amount.') from exc
    with connect() as db:
        count = sum(insert(db, row) for row in rows)
    return {'imported': count, 'duplicates_skipped': len(rows) - count}


@app.post('/api/simulate', dependencies=[Depends(authorize)])
def simulate(body: Scenario):
    with connect() as db:
        rows = [dict(r) for r in db.execute('SELECT * FROM transactions')]
        balance = db.execute('SELECT balance FROM settings WHERE id=1').fetchone()[0]
    try:
        report = Coordinator().run(rows, balance, date.today(), scenario=body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {**report['simulation'], 'orchestration': report['orchestration'],
            'execution_trace': report['execution_trace']}


@app.get('/api/purchase-scenarios', dependencies=[Depends(authorize)])
def list_purchase_scenarios():
    with connect() as db:
        return [dict(id=row['id'], created_at=row['created_at'], **json.loads(row['payload']))
                for row in db.execute('SELECT * FROM purchase_scenarios ORDER BY id DESC LIMIT 100')]


@app.post('/api/purchase-scenarios', status_code=201, dependencies=[Depends(authorize)])
def save_purchase_scenario(body: NamedPurchase):
    days = (body.planned_date - date.today()).days
    with connect() as db:
        rows = [dict(r) for r in db.execute('SELECT * FROM transactions')]
        balance = db.execute('SELECT balance FROM settings WHERE id=1').fetchone()[0]
    try:
        report = Coordinator().run(rows, balance, date.today(), scenario={
            'cost': body.cost, 'days': days,
            'reserve': body.reserve, 'monthly_saving': body.monthly_saving
        })
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    payload = {**body.model_dump(mode='json'), 'days': days,
               'simulation': report['simulation'],
               'evidence': {'as_of': report['as_of'],
                            'transaction_ids': [r['id'] for r in rows],
                            'balance': balance},
               'assumptions': report['simulation'].get('assumptions')}
    created = datetime.now(timezone.utc).isoformat()
    with connect() as db:
        cursor = db.execute('INSERT INTO purchase_scenarios(created_at,payload) VALUES(?,?)',
                            (created, json.dumps(payload)))
        scenario_id = cursor.lastrowid
    return {'id': scenario_id, 'created_at': created, **payload}


@app.delete('/api/purchase-scenarios/{scenario_id}', dependencies=[Depends(authorize)])
def delete_purchase_scenario(scenario_id: int):
    with connect() as db:
        changed = db.execute('DELETE FROM purchase_scenarios WHERE id=?', (scenario_id,)).rowcount
    if not changed:
        raise HTTPException(404, 'Scenario not found')
    return {'deleted': True}


@app.post('/api/chat', dependencies=[Depends(authorize)])
def chat(body: Question):
    try:
        report = dashboard()
        trace = []
        answer = converse(body.question, report, lambda args: simulate(Scenario.model_validate(args)), trace, [m.model_dump() for m in body.history])
        save_report({**report, 'question': body.question, 'answer': answer, 'provider': 'Mesh API', 'tool_trace': trace})
        return {'answer': answer, 'provider': 'Mesh API', 'tool_trace': trace}
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        if getattr(exc, 'status_code', None) == 402:
            raise HTTPException(503, 'Mesh rejected this request with HTTP 402 (spending limit or credit restriction). Check your Mesh account limits or contact the hackathon organizer about sponsor credits. Sample-data analysis and simulations still work.') from exc
        raise HTTPException(502, 'Mesh request failed; check API availability, key and model.') from exc


@app.post('/api/digest', dependencies=[Depends(authorize)])
def digest():
    return save_digest()


@app.get('/api/audit', dependencies=[Depends(authorize)])
def audit():
    with connect() as db:
        return [{'id': r['id'], 'created_at': r['created_at'], 'report': json.loads(r['payload'])}
                for r in db.execute('SELECT * FROM reports ORDER BY id DESC LIMIT 30')]


@app.get('/api/transactions', dependencies=[Depends(authorize)])
def transactions():
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM transactions ORDER BY date DESC LIMIT 500')]


@app.post('/api/demo', dependencies=[Depends(authorize)])
def demo():
    from .sample import sample_transactions
    with connect() as db:
        if db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]:
            raise HTTPException(409, 'Demo data can only be loaded into an empty database.')
        rows = sample_transactions()
        for row in rows:
            insert(db, Transaction.model_validate(row))
        db.execute('UPDATE settings SET balance=45000 WHERE id=1')
    save_digest()
    return {'imported': len(rows)}


@app.get('/api/subscriptions', dependencies=[Depends(authorize)])
def get_subscriptions():
    return dashboard()['subscriptions']


@app.put('/api/subscriptions', dependencies=[Depends(authorize)])
def put_subscription(body: planning.Subscription):
    with connect() as db:
        db.execute('INSERT INTO subscriptions VALUES(?,?) ON CONFLICT(merchant) DO UPDATE SET payload=excluded.payload', (body.merchant, body.model_dump_json()))
    return {'saved':True}


@app.get('/api/goals', dependencies=[Depends(authorize)])
def get_goals():
    return dashboard()['goals']


@app.post('/api/goals', dependencies=[Depends(authorize)])
def create_goal(body: planning.Goal):
    if not 0 <= (body.deadline-date.today()).days <= 730:
        raise HTTPException(422, 'Deadline must be within the next two years.')
    with connect() as db:
        result = db.execute('INSERT INTO goals(payload) VALUES(?)', (body.model_dump_json(),))
        return {'id':result.lastrowid}


@app.put('/api/goals/{goal_id}', dependencies=[Depends(authorize)])
def update_goal(goal_id: int, body: planning.Goal):
    if not 0 <= (body.deadline-date.today()).days <= 730:
        raise HTTPException(422, 'Deadline must be within the next two years.')
    with connect() as db:
        if not db.execute('UPDATE goals SET payload=? WHERE id=?', (body.model_dump_json(),goal_id)).rowcount:
            raise HTTPException(404,'Goal not found')
    return {'saved':True}


@app.delete('/api/goals/{goal_id}', dependencies=[Depends(authorize)])
def delete_goal(goal_id: int):
    with connect() as db:
        if not db.execute('DELETE FROM goals WHERE id=?', (goal_id,)).rowcount:
            raise HTTPException(404,'Goal not found')
    return {'deleted':True}


def deliver_daily(report):
    day = datetime.now(timezone.utc).date().isoformat()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT * FROM deliveries WHERE day=?',(day,)).fetchone()
        if old and (old['status'] in ('sent','sending','unknown') or old['attempts']>=3):
            return {'status':old['status']}
        if old: report=json.loads(old['payload'])
        attempts = old['attempts']+1 if old else 1
        db.execute('INSERT INTO deliveries VALUES(?,?,?,?) ON CONFLICT(day) DO UPDATE SET status=excluded.status,attempts=excluded.attempts', (day,'sending',attempts,json.dumps(report)))
    try:
        delivery.send(report)
        status='sent'
    except delivery.DeliveryUncertain:
        status='unknown'
    except Exception:
        status='failed'
    with connect() as db:
        db.execute('UPDATE deliveries SET status=? WHERE day=?',(status,day))
    return {'status':status}


def scheduled_digest():
    report=save_digest()
    if delivery.configuration()['enabled']:
        deliver_daily(report)


@app.get('/api/delivery', dependencies=[Depends(authorize)])
def delivery_status():
    with connect() as db:
        records=[dict(r) for r in db.execute('SELECT day,status,attempts FROM deliveries ORDER BY day DESC LIMIT 10')]
    return {**delivery.configuration(), 'records':records, 'hour_utc':int(os.getenv('DIGEST_HOUR_UTC','3'))}


@app.post('/api/digest/send', dependencies=[Depends(authorize)])
def email_digest():
    config=delivery.configuration()
    if not config['enabled'] or not config['configured']:
        raise HTTPException(409,'Configure and enable email locally first.')
    result=deliver_daily(save_digest())
    if result['status']!='sent':
        raise HTTPException(502,'Delivery status: '+result['status']+'. Check delivery settings or mailbox before retrying.')
    return result
