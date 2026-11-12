"""Compare a MySQL-trained Ridge/CatBoost package against the deployed weights."""
from datetime import timedelta
from uuid import uuid4
import json
import math
import pickle

import pandas as pd

from Models import research as r
from api.forecasting import ROOT

CONFIG = {
    'monthly': ('RidgeShort|total|log', 3),
    'weekly': ('CatBoost|sku|raw', 12),
}
BASELINE = 'Rolling mean|total|raw'
METRICS = ('total_wape_pct', 'total_mae', 'total_rmse', 'sku_wape_pct', 'sku_mae', 'sku_rmse')
MAX_WAPE_PCT = 100.0  # WAPE of an all-zero forecast when actual sales are positive.


def passes_gate(incumbent, candidate):
    pairs = [(incumbent[name], candidate[name]) for name in METRICS]
    return (
        all(old is not None and new is not None and math.isfinite(old) and math.isfinite(new)
            for old, new in pairs)
        and max(candidate['total_wape_pct'], candidate['sku_wape_pct']) <= MAX_WAPE_PCT
        and all(new <= old for old, new in pairs)
        and any(new < old for old, new in pairs)
    )


def evaluation_frame(history, holdout, target, frequency, candidate, model, columns, horizon):
    forecast = r.predict(history, target, frequency, candidate, model, columns, horizon)
    scored = forecast.merge(
        holdout[['product_id', 'period', target]], on=['product_id', 'period'],
        how='outer', validate='one_to_one',
    ).rename(columns={target: 'actual'})
    if scored[['actual', 'forecast']].isna().any().any() or scored.period.nunique() != horizon:
        raise ValueError('Evaluation predictions do not cover the same products and periods.')
    return scored.assign(
        origin=holdout.period.min().isoformat(), frequency=frequency, target=target,
        model=candidate, partition='test',
    )


def train(daily, incumbent_folder):
    with (incumbent_folder / 'package.pkl').open('rb') as file:
        incumbent = pickle.load(file)
    previous = incumbent['metadata']
    cutoff = pd.Timestamp(previous.get('retrain_evaluated_through', previous['data_cutoff']))
    last_sale = daily.sale_date.max()
    panels = {frequency: r.panel(daily, frequency) for frequency in CONFIG}
    windows = {}
    for frequency, (_, horizon) in CONFIG.items():
        panel = panels[frequency]
        dates = sorted(panel.loc[panel.period > cutoff, 'period'].unique())
        if len(dates) < horizon:
            return None, [], (
                'Keep current model. Retrain needs 3 complete months and 12 complete weeks '
                f'after {cutoff.date()} for evaluation. Available {frequency} periods: {len(dates)}.'
            )
        origin = dates[-horizon]
        history, holdout = panel[panel.period < origin], panel[panel.period >= origin]
        if history.period.max() <= incumbent['panels'][frequency].period.max():
            return None, [], f'Keep current model. Wait for new complete {frequency} training data before the test window.'
        known_products = set(daily.loc[daily.sale_date < origin, 'product_id'])
        if known_products != set(panel.product_id):
            return None, [], f'Keep current model. Some products have no history before the {frequency} test window.'
        windows[frequency] = (history, holdout)

    metadata = {
        'run_id': 'retrain_' + uuid4().hex,
        'profile': 'mysql-admin-retrain',
        'source': 'mysql:Daily_Sales',
        'data_cutoff': last_sale.date().isoformat(),
        'retrain_evaluated_through': last_sale.date().isoformat(),
        'reference_run': previous['run_id'],
        'currency': 'GBP',
        'is_simulated': True,
        'selections': {},
    }
    package = dict(metadata=metadata, models={}, columns={}, panels={})
    evaluation, scored_frames = [], []
    for frequency, (candidate, horizon) in CONFIG.items():
        history, holdout = windows[frequency]
        package['panels'][frequency] = history
        period_end = pd.offsets.MonthEnd(0) if frequency == 'monthly' else timedelta(days=6)
        for target in r.TARGETS:
            key = f'{frequency}:{target}'
            champion = previous['selections'][key]['champion']
            model, columns = r.fit_forecaster(history, target, frequency, candidate)
            candidate_frame = evaluation_frame(history, holdout, target, frequency, candidate, model, columns, horizon)
            deployed_key = (key, champion)
            incumbent_frame = evaluation_frame(
                history, holdout, target, frequency, champion,
                incumbent['models'][deployed_key], incumbent['columns'][deployed_key], horizon,
            )
            before, after = r.metrics(incumbent_frame), r.metrics(candidate_frame)
            # Reject non-finite scores and represent undefined metrics as null in JSON.
            passed = passes_gate(before, after)
            evaluation.append(dict(
                frequency=frequency, target=target, periods=horizon,
                start=holdout.period.min().date().isoformat(),
                end=(holdout.period.max() + period_end).date().isoformat(), incumbent_model=champion,
                candidate_model=candidate, passed=passed,
                incumbent={name: before[name] if before[name] is not None and math.isfinite(before[name]) else None for name in METRICS},
                candidate={name: after[name] if after[name] is not None and math.isfinite(after[name]) else None for name in METRICS},
            ))
            metadata['selections'][key] = dict(champion=candidate, best_ml=candidate, baseline=BASELINE)
            package['models'][(key, candidate)] = model
            package['columns'][(key, candidate)] = columns
            baseline, baseline_columns = r.fit_forecaster(history, target, frequency, BASELINE)
            package['models'][(key, BASELINE)] = baseline
            package['columns'][(key, BASELINE)] = baseline_columns
            scored_frames.extend([
                candidate_frame,
                evaluation_frame(history, holdout, target, frequency, BASELINE, baseline, baseline_columns, horizon),
            ])

    if not all(row['passed'] for row in evaluation):
        return None, evaluation, 'Keep current model. One or more forecast series did not pass the error checks.'

    metadata['retrain_gate'] = evaluation
    predictions = pd.concat(scored_frames, ignore_index=True)
    score_keys = ['frequency', 'target', 'model', 'partition', 'horizon']
    scores = pd.DataFrame([
        dict(zip(score_keys, keys), **r.metrics(group))
        for keys, group in predictions.groupby(score_keys)
    ])
    metadata['scores'] = json.loads(scores.to_json(orient='records'))
    folder = ROOT / 'runtime' / 'retrain' / metadata['run_id']
    folder.mkdir(parents=True, exist_ok=False)
    predictions.to_csv(folder / 'predictions.csv', index=False)
    scores.to_csv(folder / 'scores.csv', index=False)
    origins = predictions.groupby('frequency').origin.first()
    scores.assign(origin=scores.frequency.map(origins)).to_csv(folder / 'origin_scores.csv', index=False)
    (folder / 'metadata.json').write_text(json.dumps(metadata, allow_nan=False), encoding='utf-8')
    with (folder / 'package.pkl').open('wb') as file:
        pickle.dump(package, file)
    # Keep the exact weights scored above. Holdout actuals can be used as lag inputs
    # for forecasts after the test window.
    r.inference(package, daily, {frequency: spec[1] for frequency, spec in CONFIG.items()}).to_csv(
        folder / 'forecasts.csv', index=False,
    )
    return folder, evaluation, 'Retrained model passed the evaluation and is active.'
