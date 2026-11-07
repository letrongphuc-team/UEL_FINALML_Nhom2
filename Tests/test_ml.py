from datetime import timedelta
from pathlib import Path
import json,pickle
import numpy as np
import pandas as pd
from Models.research import features,panel,inference
ROOT=Path(__file__).resolve().parents[1]

def test_artifact_inference_without_fit(monkeypatch):
    manifest=json.loads((ROOT/'Models/active_plan_a.json').read_text())
    folder=ROOT/manifest['path']
    with (folder/'package.pkl').open('rb') as f:package=pickle.load(f)
    for model in package['models'].values():
        if model is not None:monkeypatch.setattr(model,'fit',lambda *a,**k:(_ for _ in ()).throw(AssertionError('Inference must not fit')))
    daily=pd.read_csv(folder/'daily_snapshot.csv',dtype={'product_id':str},parse_dates=['sale_date'])
    expected=pd.read_csv(folder/'forecasts.csv');actual=inference(package,daily)
    assert np.allclose(expected.forecast,actual.forecast)
    assert panel(daily,'monthly').period.max()==pd.Timestamp('2011-11-01')
    weekly=panel(daily,'weekly');assert weekly.period.max()+timedelta(days=6)<=daily.sale_date.max()

def test_features_use_only_past():
    dates=pd.date_range('2020-01-01',periods=18,freq='MS')
    frame=pd.DataFrame(dict(product_id='A',category='shampoo',period=dates,quantity_sold=np.arange(18)))
    first,cols=features(frame,'quantity_sold','monthly');frame.loc[17,'quantity_sold']=999999
    second,_=features(frame,'quantity_sold','monthly')
    assert first[cols].equals(second[cols])
    assert first.loc[17,'mean_3']==15

def test_weekly_validation_does_not_overlap_test():
    manifest=json.loads((ROOT/'Models/active_plan_a.json').read_text());folder=ROOT/manifest['path']
    frame=pd.read_csv(folder/'predictions.csv');weekly=frame[frame.frequency=='weekly']
    assert set(weekly[weekly.partition=='validation'].period).isdisjoint(weekly[weekly.partition=='test'].period)
