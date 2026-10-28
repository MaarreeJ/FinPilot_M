"""Explicit user-confirmed subscriptions and savings plans; no money movement."""
from datetime import date
from collections import defaultdict
from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Literal
from .agents import money


class Subscription(BaseModel):
    model_config = ConfigDict(extra='forbid')
    merchant: str = Field(min_length=1, max_length=200)
    amount: float = Field(gt=0, le=1e7, allow_inf_nan=False)
    cycle: Literal['monthly', 'annual', 'weekly'] = 'monthly'
    usage: Literal['unknown', 'used', 'unused'] = 'unknown'
    active: bool = True

    @field_validator('merchant')
    @classmethod
    def clean(cls, v):
        v = ' '.join(v.split())
        if not v:
            raise ValueError('Merchant required')
        return v


class Goal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    target: float = Field(gt=0, le=1e9, allow_inf_nan=False)
    saved: float = Field(ge=0, le=1e9, allow_inf_nan=False)
    monthly: float = Field(ge=0, le=1e9, allow_inf_nan=False)
    deadline: date

    @field_validator('name')
    @classmethod
    def clean(cls, v):
        if not v.strip():
            raise ValueError('Name required')
        return v.strip()


def subscriptions(rows, confirmed, as_of):
    grouped = defaultdict(list)
    for row in rows:
        if row['category'] == 'subscriptions' and row['amount'] < 0 and 0 <= (as_of-date.fromisoformat(row['date'])).days <= 400:
            grouped[' '.join(row['merchant'].lower().split())].append(row)
    prefs = {s['merchant'].lower(): s for s in confirmed}
    result = []
    for key in sorted(grouped.keys() | prefs.keys()):
        charges = sorted(grouped[key], key=lambda r: r['date'])
        s = dict(prefs[key]) if key in prefs else {'merchant':charges[-1]['merchant'], 'amount':abs(charges[-1]['amount']), 'cycle':'unknown', 'usage':'unknown', 'active':None}
        s.update(confirmed=key in prefs, transaction_ids=[r['id'] for r in charges if 'id' in r])
        s['monthly_cost'] = money(s['amount'] * {'monthly':1, 'annual':1/12, 'weekly':52/12}.get(s['cycle'], 0)) if s['cycle'] != 'unknown' else None
        result.append(s)
    return result


def progress(goals, forecast, as_of):
    pending = [g for g in goals if g['saved'] < g['target'] and date.fromisoformat(g['deadline']) >= as_of]
    planned = sum(g['monthly'] for g in pending)
    capacity = max(0, forecast.get('daily_net', 0)*30)
    share = min(1, capacity/planned) if planned else 0
    result = []
    for goal in goals:
        g = dict(goal)
        days = max(0, (date.fromisoformat(g['deadline'])-as_of).days)
        allocated = g['monthly']*share if g in pending else 0
        projected = money(g['saved'] + allocated*days/30)
        status = 'achieved' if g['saved'] >= g['target'] else 'overdue' if date.fromisoformat(g['deadline']) < as_of else 'needs_history' if not forecast.get('available') else 'at_risk' if projected < g['target'] else 'on_track'
        g.update(projected=projected, shortfall=money(max(0,g['target']-projected)), status=status, allocated_monthly=money(allocated), assumption='Planned contributions capped by historical positive net cash flow, shared proportionally across goals. Savings are user-entered; no transfers occur.')
        result.append(g)
    return result


def advice(subs, goals):
    result = []
    for s in subs:
        if s['confirmed'] and s['active'] and s['usage']=='unused':
            result.append({'trigger':'unused_subscription','action':f"Consider cancelling {s['merchant']} to save approximately ₹{s['monthly_cost']:.2f} per month.", 'potential_saving':s['monthly_cost'], 'evidence':s})
    for g in goals:
        if g['status'] in ('at_risk','overdue'):
            result.append({'trigger':'goal_shortfall','action':f"Your {g['name']} goal is projected to be ₹{g['shortfall']:.2f} short by {g['deadline']}. Review the contribution or deadline.", 'evidence':g})
    return result


def digest(report):
    priorities = {'negative_forecast':0,'unused_subscription':1,'goal_shortfall':2,'spending_increase':3}
    choices = sorted(report['recommendations'], key=lambda r: priorities.get(r['trigger'], 9))
    if not choices:
        return {'insight':'No recommendation trigger was found in the available data.', 'action':'Review your transaction history and current balance for completeness.', 'evidence':{'as_of':report['as_of']}}
    item = choices[0]
    e = item['evidence']
    insight = {'negative_forecast':'Your projected balance falls below zero.', 'unused_subscription':'You confirmed an active subscription is unused.', 'goal_shortfall':'Your planned savings may not reach a goal on time.', 'spending_increase':'Recent spending increased compared with the preceding 30 days.'}[item['trigger']]
    return {'insight':insight, 'action':item['action'], 'trigger':item['trigger'], 'evidence':e}
