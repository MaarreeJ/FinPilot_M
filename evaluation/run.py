"""Reproducible synthetic evaluation. No API calls and no hidden test-data tuning."""
from datetime import date, timedelta
from pathlib import Path
import json
import random
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agents import Categorizer, Forecaster

ROOT = Path(__file__).parent


def evaluate():
    cases = json.loads((ROOT / 'categories.json').read_text())
    predictions = [{'merchant': c['merchant'], 'expected': c['expected'],
                    'predicted': Categorizer().run(c)} for c in cases]
    correct = sum(p['expected'] == p['predicted'] for p in predictions)
    rng = random.Random(20261018)
    # Train on 90 days; evaluate the next 30 without feeding them to the forecast.
    start = date(2026, 1, 1)
    windows = []
    for profile in ['stable', 'variable', 'spending_shift']:
        for trial in range(10):
            daily = []
            for day in range(120):
                expense = 900 + (rng.randint(-300, 300) if profile != 'stable' else 0)
                if profile == 'spending_shift' and day >= 90:
                    expense += 500
                daily.append((60000 if day % 30 == 0 else 0) - expense)
            rows = [{'date': str(start + timedelta(days=i)), 'amount': n} for i, n in enumerate(daily[:90])]
            predicted = Forecaster().run(rows, 45000, start + timedelta(days=89))['projected_balance']
            actual = 45000 + sum(daily[90:])
            windows.append({'profile': profile, 'trial': trial, 'predicted': predicted,
                            'actual': actual, 'absolute_error': round(abs(predicted - actual), 2)})
    return {'dataset': '25 hand-labeled synthetic merchant cases; 30 seeded synthetic forecast holdouts',
            'categorization': {'correct': correct, 'total': len(cases),
                               'accuracy': round(correct / len(cases), 4), 'predictions': predictions},
            'forecast': {'horizon_days': 30, 'train_days': 90, 'holdout_windows': len(windows),
                         'mae_inr': round(statistics.mean(w['absolute_error'] for w in windows), 2),
                         'mae_by_profile': {p: round(statistics.mean(w['absolute_error'] for w in windows if w['profile'] == p), 2)
                                            for p in ['stable', 'variable', 'spending_shift']}, 'windows': windows},
            'limitations': 'Synthetic, small, developer-authored evaluation. Not a real-world benchmark. Forecast cannot predict unseen spending shifts. Positive refunds are currently treated as income. Live Mesh answer quality not evaluated.'}


if __name__ == '__main__':
    results = evaluate()
    (ROOT / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps({k: v for k, v in results.items() if k not in ['categorization', 'forecast']}, indent=2))
    print('Categorization accuracy:', results['categorization']['accuracy'])
    print('Forecast MAE INR:', results['forecast']['mae_inr'])
    print('Forecast by profile:', results['forecast']['mae_by_profile'])
