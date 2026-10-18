from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
import csv
import io
import json
import os
import secrets
import sqlite3

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
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
    report = Coordinator().run(rows, balance, date.today())
    report['transaction_ids'] = [row['id'] for row in rows]
    return report


def save_report(report):
    with connect() as db:
        db.execute('INSERT INTO reports(created_at,payload) VALUES(?,?)',
                   (datetime.now(timezone.utc).isoformat(), json.dumps(report)))
    return report


def save_digest():
    return save_report(dashboard())


@asynccontextmanager
async def lifespan(app):
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler(timezone='UTC')
    scheduler.add_job(save_digest, 'cron', hour=int(os.getenv('DIGEST_HOUR_UTC', '3')),
                      id='daily_digest', max_instances=1, coalesce=True)
    app.state.scheduler = scheduler
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title='FinPilot', version='0.1.0', lifespan=lifespan)


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


class Question(BaseModel):
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
    return {'status': 'ok', 'mesh_configured': bool(os.getenv('MESH_API_KEY'))}


@app.get('/api/dashboard', dependencies=[Depends(authorize)])
def get_dashboard():
    return save_digest()


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
    from .agents import Forecaster
    with connect() as db:
        rows = [dict(r) for r in db.execute('SELECT * FROM transactions')]
        balance = db.execute('SELECT balance FROM settings WHERE id=1').fetchone()[0]
    forecast = Forecaster().run(rows, balance, date.today(), body.days)
    if not forecast['available']:
        raise HTTPException(422, forecast['reason'])
    after = money(forecast['projected_balance'] - body.cost + body.monthly_saving * body.days / 30)
    return {'baseline': forecast['projected_balance'], 'after_purchase': after,
            'reserve': body.reserve, 'affordable_under_model': after >= body.reserve,
            'assumptions': 'One-time purchase at horizon; savings accrue evenly. End balance only; payment timing is not modeled.',
            'forecast': forecast}


@app.post('/api/chat', dependencies=[Depends(authorize)])
def chat(body: Question):
    try:
        report = dashboard()
        answer = converse(body.question, report)
        save_report({**report, 'question': body.question, 'answer': answer, 'provider': 'Mesh API'})
        return {'answer': answer, 'provider': 'Mesh API'}
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, 'Mesh request failed; check gateway availability, key and model.') from exc


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
