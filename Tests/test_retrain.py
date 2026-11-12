import json
import pickle
import shutil

import pandas as pd
import pytest

from Models import research, retrain
from api import forecasting
from api.business import BusinessError


@pytest.fixture(scope='module')
def original_run():
    return forecasting.active_folder()


@pytest.fixture(scope='module')
def original_daily(original_run):
    return pd.read_csv(original_run / 'daily_snapshot.csv', dtype={'product_id': str}, parse_dates=['sale_date'])


def test_gate_requires_finite_non_regressing_errors_and_improvement():
    incumbent = dict.fromkeys(retrain.METRICS, 120.0)
    candidate = dict.fromkeys(retrain.METRICS, 20.0)
    assert retrain.passes_gate(incumbent, candidate)
    assert not retrain.passes_gate(candidate, candidate)
    for metric in retrain.METRICS:
        assert not retrain.passes_gate(incumbent, {**candidate, metric: 121.0})
        assert not retrain.passes_gate(incumbent, {**candidate, metric: None})
        assert not retrain.passes_gate(incumbent, {**candidate, metric: float('inf')})
    assert not retrain.passes_gate(incumbent, {**candidate, 'total_wape_pct': 101.0})
    assert not retrain.passes_gate(incumbent, {**candidate, 'sku_wape_pct': 101.0})


def test_old_sales_only_are_rejected(original_run, original_daily):
    folder, evaluation, message = retrain.train(original_daily, original_run)
    assert folder is None and not evaluation
    assert '3 complete months and 12 complete weeks' in message


def test_one_regression_blocks_package(tmp_path, monkeypatch, original_run, original_daily):
    extension = original_daily[original_daily.sale_date.between('2010-12-01', '2011-06-30')].copy()
    extension['sale_date'] += pd.DateOffset(years=1)
    daily = pd.concat([original_daily[original_daily.sale_date < '2011-12-01'], extension])
    monkeypatch.setattr(retrain, 'ROOT', tmp_path)
    folder, evaluation, _ = retrain.train(daily, original_run)
    assert folder is None
    assert any(row['passed'] for row in evaluation) and not all(row['passed'] for row in evaluation)
    assert not (tmp_path / 'runtime').exists()


def test_accepted_package_is_used_after_sql_publish(tmp_path, monkeypatch, original_run, original_daily):
    products = original_daily.drop_duplicates('product_id')[['product_id', 'category', 'product_name']]
    dates = pd.DataFrame({'sale_date': pd.date_range('2011-12-01', '2013-12-31')})
    extension = products.merge(dates, how='cross').assign(quantity_sold=20., revenue_gbp=50.)
    daily = pd.concat([original_daily[original_daily.sale_date < '2011-12-01'], extension])

    incumbent_path = tmp_path / 'Models' / 'results' / original_run.name
    shutil.copytree(original_run, incumbent_path)
    manifest = tmp_path / 'Models' / 'active_plan_a.json'
    manifest.write_text(json.dumps({'path': str(incumbent_path.relative_to(tmp_path))}))
    monkeypatch.setattr(retrain, 'ROOT', tmp_path)
    monkeypatch.setattr(forecasting, 'ROOT', tmp_path)
    folder, evaluation, _ = retrain.train(daily, incumbent_path)
    assert len(evaluation) == 4 and all(row['passed'] for row in evaluation)
    assert forecasting.active_folder() == incumbent_path
    with (folder / 'package.pkl').open('rb') as file:
        package = pickle.load(file)
    for row in evaluation:
        assert package['panels'][row['frequency']].period.max() < pd.Timestamp(row['start'])

    class ForecastStore:
        latest = None

        def one(self, sql, params=()):
            if 'GET_LOCK' in sql:
                return {'acquired': 1}
            if 'FROM Forecast_Run' in sql:
                return self.latest
            if 'FROM Product_Variant' in sql:
                return dict(variant_id=1, on_hand=0, reserved=0, incoming=0)
            assert 'SELECT DATABASE()' in sql
            return {'name': 'test_database'}

        def all(self, sql):
            if sql == forecasting.DAILY_SQL:
                return daily.to_dict('records')
            assert sql == 'SELECT * FROM Settings'
            return []

        def execute(self, sql, params):
            if sql.startswith('INSERT INTO Forecast_Run'):
                self.latest = dict(zip(('run_id', 'metadata_json', 'predictions_json'), params))
            return 1

    store = ForecastStore()
    with pytest.raises(BusinessError, match='Daily_Sales changed'):
        forecasting.run_inference(store, folder, expected_incumbent=incumbent_path, expected_fingerprint='stale')
    assert store.latest is None
    result = forecasting.run_inference(
        store, folder, expected_incumbent=incumbent_path, expected_fingerprint=forecasting.database_fingerprint(daily),
    )
    assert result['metadata']['run_id'] == folder.name
    assert forecasting.active_folder(store.latest) == folder
    assert forecasting.report(store)['metadata']['run_id'] == folder.name

    def no_fit(*args):
        raise AssertionError('Refreshing forecasts must use the approved fitted weights.')

    monkeypatch.setattr(research, 'fit_forecaster', no_fit)
    refreshed = forecasting.run_inference(store)
    assert refreshed['metadata']['run_id'] == folder.name
    assert len(result['forecasts']) == len(refreshed['forecasts']) == 59 * (3 + 12) * 2 * 3
    assert manifest.read_text() == json.dumps({'path': str(incumbent_path.relative_to(tmp_path))})
