import json,pickle
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from api.forecasting import active_folder,analysis_folder,report
from api.analytics import historical_data
from Models.research import inference

@pytest.fixture(scope='module')
def data():
    folder=active_folder()
    with (folder/'package.pkl').open('rb') as file:package=pickle.load(file)
    daily=pd.read_csv(folder/'daily_snapshot.csv',dtype={'product_id':str},parse_dates=['sale_date'])
    return folder,package,daily

def test_extended_projection_uses_fitted_package_and_preserves_first_steps(data,monkeypatch):
    folder,package,daily=data
    for model in package['models'].values():
        if model is not None:
            def forbidden(*args,**kwargs):raise AssertionError('Live inference must not fit')
            monkeypatch.setattr(model,'fit',forbidden)
    result=inference(package,daily,{'monthly':3,'weekly':12})
    assert len(result)==5310
    assert result.groupby('frequency').horizon.max().to_dict()=={'monthly':3,'weekly':12}
    assert (result.forecast>=0).all() and np.isfinite(result.forecast).all()
    original=pd.read_csv(folder/'forecasts.csv',dtype={'product_id':str})
    old_steps=result[((result.frequency=='monthly')&(result.horizon==1))|((result.frequency=='weekly')&(result.horizon<=2))]
    keys=['frequency','target','role','product_id','horizon']
    merged=original.merge(old_steps,on=keys,suffixes=('_old','_new'),validate='one_to_one')
    assert len(merged)==len(original)
    assert np.allclose(merged.forecast_old,merged.forecast_new)

@pytest.mark.parametrize('horizons',[{'monthly':4},{'weekly':13},{'monthly':0},{'weekly':True}])
def test_horizon_limits_reject_invalid_requests(data,horizons):
    _,package,daily=data
    with pytest.raises(ValueError):inference(package,daily,horizons)

def test_historical_panels_and_full_span_zero_statistics(data):
    _,_,daily=data
    history=historical_data(daily,'test snapshot')
    monthly=pd.DataFrame(history['monthly']);weekly=pd.DataFrame(history['weekly'])
    assert history['complete_month_end']=='2011-11-30' and history['complete_week_end']=='2011-12-04'
    assert not monthly.period.str.startswith('2011-12').any()
    expected=daily[daily.sale_date<=pd.Timestamp('2011-11-30')]
    assert np.isclose(monthly.revenue_gbp.sum(),expected.revenue_gbp.sum())
    assert weekly.period.map(lambda p:pd.Timestamp(p).weekday()).eq(0).all()
    quality=pd.DataFrame(history['quality'])
    assert quality.zero_days.sum()==(daily.quantity_sold==0).sum()
    assert quality.zero_day_pct.between(0,100).all()

def test_extended_diagnostics_precede_published_tests_and_cover_horizons(data):
    _,package,_=data
    folder=analysis_folder();assert folder is not None
    predictions=pd.read_csv(folder/'horizon_predictions.csv')
    scores=pd.read_csv(folder/'horizon_scores.csv')
    for frequency,maximum in [('monthly',3),('weekly',12)]:
        rows=predictions[predictions.frequency==frequency]
        boundary=pd.Timestamp(package['metadata']['evaluation_origins'][frequency]['previously_inspected_test'])
        assert (pd.to_datetime(rows.period)<boundary).all()
        assert set(rows.horizon)==set(range(1,maximum+1))
    assert scores.origins.gt(0).all()
    assert predictions.partition.eq('retrospective_diagnostic').all()
    result=report()
    assert result['metadata']['selections']==package['metadata']['selections']
    assert max(r['horizon'] for r in result['forecasts'] if r['frequency']=='weekly')==12
