from pathlib import Path
import hashlib,json,pickle
import numpy as np
import pandas as pd
from Models.coursework_models import raw_short_features,CalendarRegressor
from Models.research import inference,features,aggregate
from api.forecasting import active_folder,analysis_folder,database_fingerprint


def test_short_features_do_not_use_current_or_future_target():
    frame=pd.DataFrame({'product_id':['A']*18,'category':['shampoo']*18,'product_name':['A']*18,
        'period':pd.date_range('2010-01-01',periods=18,freq='MS'),'quantity_sold':np.arange(18,dtype=float)+1})
    first,cols=raw_short_features(frame,'quantity_sold','monthly')
    frame.loc[17,'quantity_sold']=9999999
    second,_=raw_short_features(frame,'quantity_sold','monthly')
    assert first[cols+['scale']].equals(second[cols+['scale']])
    assert first.loc[17,'short_mean_3']==16


def test_short_features_retain_more_monthly_training_rows():
    frame=pd.DataFrame({'product_id':['TOTAL']*18,'category':['all']*18,'product_name':['Total']*18,
        'period':pd.date_range('2010-01-01',periods=18,freq='MS'),'quantity_sold':np.arange(18,dtype=float)+1})
    short,cols=raw_short_features(frame,'quantity_sold','monthly')
    legacy,old_cols=features(frame,'quantity_sold','monthly')
    assert len(short.dropna(subset=cols+['scale']))==15
    assert len(legacy.dropna(subset=old_cols))==6


def test_calendar_design_is_target_independent():
    frame=pd.DataFrame({'period':pd.date_range('2010-01-01',periods=18,freq='MS'),'quantity_sold':np.arange(18)})
    model=CalendarRegressor(frequency='monthly');a=model.design(frame)
    frame['quantity_sold']=9999999
    assert np.array_equal(a,model.design(frame))


def test_active_coursework_package_has_frozen_selection_and_honest_gate():
    folder=active_folder();meta=json.loads((folder/'metadata.json').read_text())
    assert meta['profile']=='coursework-v3-monthly'
    assert meta['test_use']=={'candidate_selection':False,'deployment_acceptance':True,'scope':'Previously inspected retrospective backtest; no hyperparameter tuning on test'}
    frozen=folder/'selections_frozen_before_test.json'
    assert hashlib.sha256(frozen.read_bytes()).hexdigest()==meta['selection_frozen_sha256']
    assert all(len(row['validation'])==8 for row in meta['evaluation_origins'].values())
    for key,result in meta['improvement_comparison'].items():
        if result['promoted']:
            assert key.startswith('monthly:')
            assert result['validation_after']['total_wape_pct']<=result['validation_before']['total_wape_pct']*.97
            assert result['validation_after']['sku_wape_pct']<=result['validation_before']['sku_wape_pct']*1.10
            for metric in ('total_wape_pct','sku_wape_pct','total_mae','total_rmse'):assert result['test_after'][metric]<=result['test_before'][metric]
        if key.startswith('weekly:'):assert result['selected_model']==result['reference_model']


def test_weekly_forecasts_are_unchanged_from_reference():
    folder=active_folder();root=Path(__file__).resolve().parents[1]
    reference=root/'Models/results/plan_a_v2/20261107_215317_027878'
    with (folder/'package.pkl').open('rb') as f:new=pickle.load(f)
    with (reference/'package.pkl').open('rb') as f:old=pickle.load(f)
    daily=pd.read_csv(folder/'daily_snapshot.csv',dtype={'product_id':str},parse_dates=['sale_date'])
    assert database_fingerprint(daily)==new['metadata']['source_sha256']
    a=inference(new,daily);b=inference(old,daily)
    a=a[a.frequency=='weekly'];b=b[b.frequency=='weekly']
    keys=['frequency','target','role','product_id','horizon'];joined=a.merge(b,on=keys,validate='one_to_one',suffixes=('_new','_old'))
    assert len(joined)==len(a) and np.allclose(joined.forecast_new,joined.forecast_old)
