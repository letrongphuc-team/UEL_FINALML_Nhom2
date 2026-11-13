"""Safety stock policy, OS-independent fingerprint and de-duplicated inference (no MySQL needed)."""
import math
import os
import pickle

import pandas as pd
import pytest

from api.forecasting import (active_folder, database_fingerprint, inventory_policy, order_plan,
                             sku_error_sigma)
from Models import research


def test_policy_reads_settings_and_service_level():
    policy = inventory_policy({'lead_time_days': '7', 'review_period_days': '30', 'safety_stock': '15', 'service_level_pct': '95'})
    assert (policy['lead'], policy['review'], policy['floor']) == (7, 30, 15.0)
    assert policy['z'] == pytest.approx(1.6449, abs=1e-4)
    assert inventory_policy({'service_level_pct': '0'})['z'] == 0
    assert inventory_policy({})['service_level_pct'] == 95


def test_order_plan_uses_floor_and_error_based_safety_stock():
    policy = dict(lead=7, review=30, floor=15.0, z=1.65)
    plan = order_plan(310, 31, 100, policy, on_hand=80, reserved=10, incoming=20)
    statistical = 1.65 * 100 * math.sqrt(37 / 31)
    assert plan['demand'] == pytest.approx(370)
    assert plan['statistical_safety_stock'] == pytest.approx(statistical, abs=0.01)
    assert plan['safety_stock'] == plan['statistical_safety_stock']
    assert plan['suggested'] == math.ceil(370 + plan['safety_stock'] - 70 - 20)
    # Unknown or tiny error falls back to the fixed floor; stock above need orders nothing.
    assert order_plan(310, 31, None, policy, 80, 10, 20)['safety_stock'] == 15
    assert order_plan(310, 31, 0, {**policy, 'z': 0}, 10_000, 0, 0)['suggested'] == 0


def test_sku_sigma_is_backtest_rmse_of_deployed_monthly_model():
    folder = active_folder()
    with (folder / 'package.pkl').open('rb') as file:
        champion = pickle.load(file)['metadata']['selections']['monthly:quantity_sold']['champion']
    sigma = sku_error_sigma(folder, champion)
    rows = pd.read_csv(folder / 'predictions.csv', dtype={'product_id': str})
    rows = rows[(rows.frequency == 'monthly') & (rows.target == 'quantity_sold') & (rows.model == champion)]
    expected = ((rows.actual - rows.forecast) ** 2).groupby(rows.product_id).mean() ** .5
    assert len(sigma) == expected.size == 59
    assert all(sigma[key] == pytest.approx(value) and value > 0 for key, value in expected.items())


def test_fingerprint_does_not_depend_on_operating_system(monkeypatch):
    folder = active_folder()
    daily = pd.read_csv(folder / 'daily_snapshot.csv', dtype={'product_id': str}, parse_dates=['sale_date'])
    hashes = set()
    for separator in ('\n', '\r\n'):
        monkeypatch.setattr(os, 'linesep', separator)
        hashes.add(database_fingerprint(daily))
    assert len(hashes) == 1


def test_identical_roles_are_predicted_once(monkeypatch):
    folder = active_folder()
    with (folder / 'package.pkl').open('rb') as file:
        package = pickle.load(file)
    daily = pd.read_csv(folder / 'daily_snapshot.csv', dtype={'product_id': str}, parse_dates=['sale_date'])
    calls, original = [], research.predict

    def counted(*args):
        calls.append(args[3])
        return original(*args)

    monkeypatch.setattr(research, 'predict', counted)
    frame = research.inference(package, daily, {'monthly': 3, 'weekly': 12})
    unique = sum(len(set(choice.values())) for choice in package['metadata']['selections'].values())
    assert len(calls) == unique
    keys = ['frequency', 'target', 'product_id', 'period']
    champion = frame[frame.role == 'champion'].set_index(keys).forecast.sort_index()
    best = frame[frame.role == 'best_ml'].set_index(keys).forecast.sort_index()
    assert champion.equals(best) and len(frame) == 59 * (3 + 12) * 2 * 3
