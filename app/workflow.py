"""Request-scoped, read-only LangGraph orchestration of financial specialists.

No checkpoint store, database write, network call, or shared user state lives here.
Categorization happens before storage; this graph consumes that categorized snapshot.
"""
from datetime import date
from functools import lru_cache
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from . import planning
from .agents import Advisor, Analyzer, Forecaster, PurchaseSimulator


class FinancialState(TypedDict, total=False):
    subscriptions_input: list[dict]
    goals_input: list[dict]
    subscriptions: list[dict]
    goals: list[dict]
    transactions: list[dict]
    balance: float
    as_of: date
    scenario: dict | None
    analysis: dict
    forecast: dict
    simulation: dict
    recommendations: list[dict]
    execution_trace: list[str]
    report: dict


def analyze(state: FinancialState) -> dict:
    return {
        'subscriptions': planning.subscriptions(state['transactions'], state.get('subscriptions_input', []), state['as_of']),
        'analysis': Analyzer().run(state['transactions'], state['as_of']),
        'execution_trace': ['analyze'],
    }


def forecast(state: FinancialState) -> dict:
    days = state['scenario']['days'] if state.get('scenario') else 30
    projection = Forecaster().run(state['transactions'], state['balance'], state['as_of'], days)
    return {
        'goals': planning.progress(state.get('goals_input', []), projection, state['as_of']),
        'forecast': projection,
        'execution_trace': [*state['execution_trace'], 'forecast'],
    }


def simulate(state: FinancialState) -> dict:
    return {
        'simulation': PurchaseSimulator().run(state['forecast'], **state['scenario']),
        'execution_trace': [*state['execution_trace'], 'simulate'],
    }


def advise(state: FinancialState) -> dict:
    return {
        'recommendations': Advisor().run(state['analysis'], state['forecast']) + planning.advice(state['subscriptions'], state['goals']),
        'execution_trace': [*state['execution_trace'], 'advise'],
    }


def assemble(state: FinancialState) -> dict:
    trace = [*state['execution_trace'], 'assemble']
    report = {
        'as_of': state['as_of'].isoformat(),
        'balance': state['balance'],
        'analysis': state['analysis'],
        'forecast': state['forecast'],
        'recommendations': state['recommendations'],
        'orchestration': 'langgraph',
        'workflow_version': 2,
        'subscriptions':state['subscriptions'], 'goals':state['goals'],
        'execution_trace': trace,
        'agent_trace': [
            'source → previously categorized transactions',
            'analyzer → equal-window patterns',
            'forecaster → cash-flow projection',
            *(['simulator → purchase scenario'] if state.get('scenario') else []),
            'advisor → evidence-linked proposals',
        ],
    }
    if state.get('scenario'):
        report['simulation'] = state['simulation']
    return {'report': report, 'execution_trace': trace}


@lru_cache(maxsize=1)
def financial_graph():
    """Cache executable graph topology only; each invocation owns its own state."""
    graph = StateGraph(FinancialState)
    graph.add_node('analyze', analyze)
    graph.add_node('forecast', forecast)
    graph.add_node('simulate', simulate)
    graph.add_node('advise', advise)
    graph.add_node('assemble', assemble)
    graph.add_edge(START, 'analyze')
    graph.add_edge('analyze', 'forecast')
    graph.add_conditional_edges(
        'forecast',
        lambda state: 'simulate' if state.get('scenario') else 'advise',
        {'simulate': 'simulate', 'advise': 'advise'},
    )
    graph.add_edge('simulate', 'advise')
    graph.add_edge('advise', 'assemble')
    graph.add_edge('assemble', END)
    return graph.compile()
