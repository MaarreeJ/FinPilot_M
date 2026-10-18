"""Synthetic transactions for a reproducible, date-relative demo."""
from datetime import date, timedelta


def sample_transactions(as_of=None):
    as_of = as_of or date.today()
    rows = []
    for age in range(89, -1, -1):
        day = as_of - timedelta(days=age)
        # One salary, rent and subscription charge per 30-day cycle.
        entries = [('Local grocery', -260)]
        if age % 30 == 29:
            entries += [('Salary', 60000), ('Apartment rent', -18000), ('Netflix', -649), ('Spotify', -119)]
        if age % 3 == 0:
            entries.append(('Zomato', -560 if age < 30 else -400))
        if age % 7 == 0:
            entries.append(('Uber', -240))
        rows.extend({'date': day.isoformat(), 'merchant': merchant, 'amount': amount}
                    for merchant, amount in entries)
    return rows
