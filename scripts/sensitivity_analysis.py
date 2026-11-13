"""Supplementary diagnostics for the report. Does not change model selection or result folders.

1. Monthly RidgeShort: sensitivity of validation/test WAPE to the Ridge alpha.
2. Weekly CatBoost: 50/50 blend with the SKU 4-week mean, scored per horizon (h1-12).
3. Safety stock backtest: fixed 15 units vs z * per-SKU backtest RMSE.

The blend was proposed after the test period had been inspected and its h1-12 origins
overlap validation, so it is reported as a diagnostic, not as a new champion.
Usage: python -m scripts.sensitivity_analysis [--skip-weekly]
Output: runtime/analysis/<timestamp>/*.csv
"""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import argparse
import math
from datetime import datetime
from statistics import NormalDist

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import Ridge
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from api.forecasting import active_folder
from Models import research as r
from Models.coursework_models import predict as coursework_predict, raw_short_features
from Models.coursework_training import _scored_prediction, evaluation_origins


class AlphaRidge:
    """Same design as coursework ShortRegressor, with a configurable alpha."""

    def __init__(self, columns, alpha):
        self.columns, self.alpha = columns, alpha

    def fit(self, X, y):
        numeric = [c for c in self.columns if c not in ('product_id', 'category')]
        self.pre = ColumnTransformer([('cat', OneHotEncoder(handle_unknown='ignore', sparse_output=False), ['product_id', 'category']),
                                      ('num', StandardScaler(), numeric)])
        self.reg = Ridge(alpha=self.alpha).fit(self.pre.fit_transform(X[self.columns]), np.log1p(y))
        return self

    def predict(self, X):
        return np.maximum(0, np.expm1(self.reg.predict(self.pre.transform(X[self.columns]))))


class Blend:
    """weight * fitted model + (1 - weight) * SKU 4-week mean, inside the recursion."""

    def __init__(self, model, weight):
        self.model, self.weight = model, weight

    def predict(self, X):
        return self.weight * self.model.predict(X) + (1 - self.weight) * X['mean_4'].to_numpy()


def scored(frames):
    return r.metrics(pd.concat(frames, ignore_index=True))


def actuals(frame, panel, target):
    return frame.merge(panel[['product_id', 'period', target]], on=['product_id', 'period']).rename(columns={target: 'actual'})


def monthly_alpha(daily):
    panel = r.panel(daily, 'monthly')
    validation, test = evaluation_origins(panel, 'monthly')
    rows = []
    for target in r.TARGETS:
        for alpha in (1, 3, 10, 30, 100, 300, 1000):
            parts = {}
            for name, origins in (('validation', validation), ('test', [test])):
                frames = []
                for origin in origins:
                    history = panel[panel.period < origin]
                    enriched, columns = raw_short_features(r.aggregate(history), target, 'monthly')
                    train = enriched.dropna(subset=columns + ['scale', target])
                    model = AlphaRidge(columns, alpha).fit(train, train[target])
                    frame = coursework_predict(history, target, 'monthly', 'RidgeShort|total|log', model, columns, 1)
                    frames.append(actuals(frame, panel, target).assign(origin=str(origin)))
                parts[name] = scored(frames)
            rows.append(dict(target=target, alpha=alpha, deployed=alpha == 100,
                             validation_total_wape=parts['validation']['total_wape_pct'], test_total_wape=parts['test']['total_wape_pct'],
                             validation_sku_wape=parts['validation']['sku_wape_pct'], test_sku_wape=parts['test']['sku_wape_pct']))
    return pd.DataFrame(rows)


def weekly_blend(daily):
    panel = r.panel(daily, 'weekly')
    dates = sorted(panel.period.unique())
    _, test = evaluation_origins(panel, 'weekly')
    origins = dates[-24:-12:2]
    rows = []
    for target in r.TARGETS:
        for name, weight in (('CatBoost (deployed)', 1.0), ('Blend 50/50', 0.5)):
            frames = []
            for origin in origins:
                history = panel[panel.period < origin]
                model, columns = r.fit_forecaster(history, target, 'weekly', 'CatBoost|sku|raw')
                model = model if weight == 1 else Blend(model, weight)
                frame = r.predict(history, target, 'weekly', 'CatBoost|sku|raw', model, columns, 12)
                frames.append(actuals(frame[frame.period < test], panel, target).assign(origin=str(origin)))
            joined = pd.concat(frames, ignore_index=True)
            for horizon, group in [('h1-12', joined), *joined.groupby('horizon')]:
                metric = r.metrics(group)
                rows.append(dict(target=target, model=name, horizon=horizon, total_wape=metric['total_wape_pct'],
                                 sku_wape=metric['sku_wape_pct'], total_bias=metric['total_bias']))
        frames = []
        for origin in origins:
            history = panel[panel.period < origin]
            frame = r.predict(history, target, 'weekly', 'Rolling mean|total|raw', None, None, 12)
            frames.append(actuals(frame[frame.period < test], panel, target).assign(origin=str(origin)))
        metric = scored(frames)
        rows.append(dict(target=target, model='Rolling mean', horizon='h1-12', total_wape=metric['total_wape_pct'],
                         sku_wape=metric['sku_wape_pct'], total_bias=metric['total_bias']))
    return pd.DataFrame(rows)


def safety_stock(daily, service_level_pct=95, floor=15.0):
    panel = r.panel(daily, 'monthly')
    validation, test = evaluation_origins(panel, 'monthly')
    frame = pd.concat([_scored_prediction(panel, 'quantity_sold', 'monthly', 'RidgeShort|total|log', origin, 1, 'backtest')
                       for origin in validation + [test]]).sort_values(['product_id', 'period'])
    frame['error'] = frame.actual - frame.forecast
    # Sigma from earlier origins only, so each month is scored out of sample.
    frame['sigma'] = frame.groupby('product_id').error.transform(
        lambda s: s.shift().expanding(3).apply(lambda a: math.sqrt(np.nanmean(a ** 2)), raw=True))
    frame = frame.dropna(subset=['sigma'])
    z = NormalDist().inv_cdf(service_level_pct / 100)

    def evaluate(name, safety):
        stock = frame.forecast + safety
        short = np.maximum(frame.actual - stock, 0)
        return dict(policy=name, mean_safety_stock=float(np.mean(safety)), cycle_service_level_pct=float((frame.actual <= stock).mean() * 100),
                    fill_rate_pct=float((1 - short.sum() / frame.actual.sum()) * 100), shortage_units=float(short.sum()),
                    excess_units=float(np.maximum(stock - frame.actual, 0).sum()))

    statistical = np.maximum(floor, z * frame.sigma.to_numpy())
    return pd.DataFrame([evaluate(f'Fixed {floor:g} units', np.full(len(frame), floor)),
                         evaluate(f'max({floor:g}, z*sigma), {service_level_pct}% target', statistical),
                         evaluate('Fixed, same total as z*sigma', np.full(len(frame), statistical.mean()))]).assign(
        months=frame.period.nunique(), products=frame.product_id.nunique())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-weekly', action='store_true', help='Skip the slower weekly CatBoost diagnostic')
    args = parser.parse_args()
    folder = active_folder()
    daily = pd.read_csv(folder / 'daily_snapshot.csv', dtype={'product_id': str}, parse_dates=['sale_date'])
    output = ROOT / 'runtime' / 'analysis' / datetime.now().strftime('%Y%m%d_%H%M%S')
    output.mkdir(parents=True)
    tables = {'monthly_alpha_sensitivity': monthly_alpha(daily), 'safety_stock_backtest': safety_stock(daily)}
    if not args.skip_weekly:
        tables['weekly_blend_by_horizon'] = weekly_blend(daily)
    pd.set_option('display.width', 200)
    for name, table in tables.items():
        table.to_csv(output / f'{name}.csv', index=False)
        print(f'\n{name}\n{table.round(2).to_string(index=False)}')
    print(f'\nSaved to {output}')


if __name__ == '__main__':
    main()
