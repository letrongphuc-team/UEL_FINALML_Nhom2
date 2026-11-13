from pathlib import Path
from datetime import datetime,timedelta
from uuid import uuid4
import json
import hashlib
import math
import pickle
from statistics import NormalDist
import pandas as pd
from api.business import BusinessError

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_SERVICE_LEVEL_PCT=95


def inventory_policy(settings):
    """Reorder settings. service_level_pct=0 keeps the fixed safety stock only."""
    service=float(settings.get('service_level_pct',DEFAULT_SERVICE_LEVEL_PCT))
    return dict(lead=int(float(settings.get('lead_time_days',7))),review=int(float(settings.get('review_period_days',30))),
                floor=float(settings.get('safety_stock',15)),service_level_pct=service,
                z=NormalDist().inv_cdf(service/100) if service>0 else 0.0)


def sku_error_sigma(folder,champion):
    """Per-SKU RMSE of the deployed monthly quantity model on its own backtest rows.

    Reads the model's evaluation artifact (like package.pkl), never sales data. The result is
    stored in Forecast_Run metadata so the admin views read it from MySQL.
    """
    import csv
    squared={}
    with (folder/'predictions.csv').open(newline='',encoding='utf-8') as file:
        for row in csv.DictReader(file):
            if row['frequency']=='monthly' and row['target']=='quantity_sold' and row['model']==champion:
                squared.setdefault(row['product_id'],[]).append((float(row['actual'])-float(row['forecast']))**2)
    return {key:math.sqrt(sum(values)/len(values)) for key,values in squared.items()}


def order_plan(monthly_forecast,period_days,sigma,policy,on_hand,reserved,incoming):
    """Demand over lead+review days plus safety stock z*sigma*sqrt(horizon/month), never below the fixed floor."""
    horizon=policy['lead']+policy['review']
    demand=float(monthly_forecast)*horizon/period_days
    statistical=round(policy['z']*float(sigma or 0)*math.sqrt(horizon/period_days),2)
    safety=max(policy['floor'],statistical)
    available=on_hand-reserved
    suggested=max(0,math.ceil(demand+safety-available-incoming))
    return dict(demand=demand,safety_stock=safety,statistical_safety_stock=statistical,available=available,suggested=suggested)

def latest_forecast(store, *, for_update=False):
    lock = ' FOR UPDATE' if for_update else ''
    key = store.one('SELECT run_id FROM Forecast_Run ORDER BY created_at DESC,run_id DESC LIMIT 1' + lock)
    return store.one('SELECT * FROM Forecast_Run WHERE run_id=%s' + lock, (key['run_id'],)) if key else None


def active_folder(latest=None):
    metadata = json.loads(latest['metadata_json']) if latest else {}
    if metadata.get('profile') == 'mysql-admin-retrain':
        entry = {'path': metadata['model_relative_path']}
        allowed = ROOT / 'runtime' / 'retrain'
    else:
        path = ROOT / 'Models' / 'active_plan_a.json'
        if not path.exists():
            raise BusinessError('No validated Plan A package. Run Models/research.py first.', 409)
        entry = json.loads(path.read_text(encoding='utf-8'))
        allowed = ROOT / 'Models' / 'results'
    folder = (ROOT / entry['path']).resolve()
    if not folder.is_relative_to(allowed.resolve()):
        raise BusinessError('Invalid model manifest', 409)
    for name in ('package.pkl','metadata.json','scores.csv','origin_scores.csv','forecasts.csv','predictions.csv'):
        if not (folder/name).is_file(): raise BusinessError(f'Model run is incomplete: missing {name}',409)
    return folder

def report(store=None):
    latest = latest_forecast(store) if store else None
    folder = active_folder(latest)
    result={'metadata':json.loads((folder/'metadata.json').read_text(encoding='utf-8'))}
    for name in ('scores','origin_scores','forecasts'):
        frame=pd.read_csv(folder/f'{name}.csv')
        result[name]=json.loads(frame.to_json(orient='records'))
    actual=pd.read_csv(folder/'predictions.csv')
    grouped=actual.groupby(['frequency','target','model','partition','origin','period','horizon'],as_index=False)[['actual','forecast']].sum()
    result['backtest']=json.loads(grouped.to_json(orient='records'))
    extension = None if result['metadata'].get('profile') == 'mysql-admin-retrain' else analysis_folder()
    if extension:
        result['forecasts']=json.loads(pd.read_csv(extension/'forecasts.csv').to_json(orient='records'))
        result['horizon_scores']=json.loads(pd.read_csv(extension/'horizon_scores.csv').to_json(orient='records'))
        result['extension']=json.loads((extension/'metadata.json').read_text(encoding='utf-8'))
    if latest:
        metadata = json.loads(latest['metadata_json'])
        if metadata['run_id'] == result['metadata']['run_id'] and metadata.get('projection_horizons') == {'monthly': 3, 'weekly': 12}:
            result['forecasts'] = json.loads(latest['predictions_json'])
            result['inference_run_id'] = latest['run_id']
    return result

def analysis_folder():
    manifest=ROOT/'Models/active_analysis.json'
    if not manifest.exists():return None
    entry=json.loads(manifest.read_text(encoding='utf-8'))
    active=json.loads((ROOT/'Models/active_plan_a.json').read_text(encoding='utf-8'))
    if entry['model_run_id']!=active['run_id']:return None
    path=(ROOT/entry['path']).resolve()
    if not path.is_relative_to(active_folder()/'extensions'):raise BusinessError('Invalid analysis manifest',409)
    if not all((path/name).is_file() for name in ('forecasts.csv','horizon_scores.csv','history.json','metadata.json')):
        raise BusinessError('Analysis export is incomplete. Run scripts/extend_forecasts.py.',409)
    return path

DAILY_SQL = """SELECT s.sale_date,s.product_id,s.quantity_sold,s.revenue AS revenue_gbp,
    p.category,p.name AS product_name FROM Daily_Sales s JOIN Product p ON p.product_id=s.product_id
    WHERE s.is_simulated=1 ORDER BY s.product_id,s.sale_date"""


def database_daily(store):
    daily=pd.DataFrame(store.all(DAILY_SQL))
    if daily.empty:raise BusinessError('Daily_Sales is empty; no CSV fallback is allowed',409)
    daily['sale_date']=pd.to_datetime(daily.sale_date)
    daily['product_id']=daily.product_id.astype(str)
    daily[['quantity_sold','revenue_gbp']]=daily[['quantity_sold','revenue_gbp']].astype(float)
    if daily.duplicated(['product_id','sale_date']).any():raise BusinessError('Duplicate product/date in Daily_Sales',409)
    import numpy as np
    values=daily[['quantity_sold','revenue_gbp']].to_numpy()
    if not np.isfinite(values).all() or (values<0).any():raise BusinessError('Invalid sales quantity or revenue',409)
    return daily


def database_fingerprint(daily):
    # Fixed '\n' terminator: pandas otherwise uses os.linesep, giving a different hash on Windows.
    canonical=daily.sort_values(['product_id','sale_date']).to_csv(index=False,date_format='%Y-%m-%d',float_format='%.8f',lineterminator='\n')
    return hashlib.sha256(canonical.encode()).hexdigest()


def run_inference(store, folder=None, *, expected_incumbent=None, expected_fingerprint=None):
    from Models.research import inference
    # Keep the write lock through commit or rollback. The owning connection closes
    # at the end of transaction(), releasing the MySQL named lock.
    lock = store.one("SELECT GET_LOCK(SHA2(CONCAT(DATABASE(), ':folmare_forecasts'), 256), 10) AS acquired")
    if lock['acquired'] != 1:
        raise BusinessError('A forecast publish is already in progress. Try again.', 409)
    current = active_folder(latest_forecast(store, for_update=True))
    if expected_incumbent is not None and current != expected_incumbent:
        raise BusinessError('The active model changed during retraining. Run retrain again.', 409)
    folder = folder or current
    try:
        with (folder/'package.pkl').open('rb') as f: package=pickle.load(f)
    except Exception as error:
        raise BusinessError('Cannot load model package. Check active run and recorded library versions.',409) from error
    scores=package['metadata'].get('scores')
    if scores is None:
        score_key=store.one("SELECT run_id FROM Forecast_Run WHERE JSON_UNQUOTE(JSON_EXTRACT(metadata_json,'$.run_id'))=%s ORDER BY created_at DESC,run_id DESC LIMIT 1",(package['metadata']['run_id'],))
        previous=store.one('SELECT metadata_json FROM Forecast_Run WHERE run_id=%s',(score_key['run_id'],)) if score_key else None
        scores=json.loads(previous['metadata_json']).get('scores') if previous else None
    if scores is None:
        raise BusinessError('Validated model metrics are not registered. Retrain from MySQL before inference; CSV fallback is disabled.',409)
    daily=database_daily(store)
    fingerprint = database_fingerprint(daily)
    if expected_fingerprint is not None and fingerprint != expected_fingerprint:
        raise BusinessError('Daily_Sales changed during retraining. Run retrain again.', 409)
    try:
        frame=inference(package,daily,{'monthly':3,'weekly':12})
    except (ValueError,KeyError,TypeError) as error:
        raise BusinessError(f'Model input is incompatible: {error}',409) from error
    run_id='inference_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_'+uuid4().hex[:8]
    frame['inference_run_id']=run_id
    settings={r['setting_key']:r['setting_value'] for r in store.all('SELECT * FROM Settings')}
    policy=inventory_policy(settings)
    sigma=sku_error_sigma(folder,package['metadata']['selections']['monthly:quantity_sold']['champion'])
    # One catalog query instead of one query per forecast row; first variant per product as before.
    variants={}
    for row in store.all('SELECT * FROM Product_Variant ORDER BY variant_id'):
        variants.setdefault(str(row['product_id']),row)
    first_month=frame.loc[frame.frequency=='monthly','period'].min()
    cutoff=str(daily.sale_date.max().date())
    insert_forecast='''INSERT INTO Inventory_Forecast (product_id,variant_id,forecast_date,period_end,horizon_days,forecast_quantity,
            forecast_revenue,revenue_currency,model_name,run_id,target_role,frequency,data_cutoff) VALUES (%s,%s,%s,%s,%s,%s,%s,'GBP',%s,%s,%s,%s,%s)'''
    batch=[]
    for (frequency,role,product,period),group in frame.groupby(['frequency','role','product_id','period']):
        variant=variants.get(str(product))
        if not variant: raise BusinessError('Forecast product missing from catalog',409)
        start=pd.Timestamp(period); end=start+pd.offsets.MonthEnd(0) if frequency=='monthly' else start+timedelta(days=6)
        days=(end-start).days+1
        quantity=float(group.loc[group.target=='quantity_sold','forecast'].iloc[0]); revenue=float(group.loc[group.target=='revenue_gbp','forecast'].iloc[0])
        name='; '.join(group.model.unique())[:100]
        values=(str(product),variant['variant_id'],start.date(),end.date(),days,quantity,revenue,name,run_id,role,frequency,cutoff)
        if not (frequency=='monthly' and role=='champion' and start==first_month):
            batch.append(values);continue
        fid=store.execute(insert_forecast,values)
        plan=order_plan(quantity,days,sigma.get(str(product)),policy,variant['on_hand'],variant['reserved'],variant['incoming'])
        store.execute('''INSERT INTO Inventory_Recommendation (forecast_id,product_id,variant_id,current_stock,safety_stock,recommended_order_quantity,lead_time_days,review_period_days)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',(fid,str(product),variant['variant_id'],max(0,plan['available']),plan['safety_stock'],plan['suggested'],policy['lead'],policy['review']))
    store.execute_many(insert_forecast,batch)
    records=json.loads(frame.to_json(orient='records',date_format='iso'))
    metadata={**package['metadata'],'projection_horizons':{'monthly':3,'weekly':12},
        'source':'mysql:Daily_Sales','data_cutoff':str(daily.sale_date.max().date()),
        'source_sha256':fingerprint,
        'database':store.one('SELECT DATABASE() AS name')['name'],
        'source_rows':len(daily),'source_products':int(daily.product_id.nunique()),
        'scores':scores,
        'inventory_policy':{**policy,'safety_stock':'max(floor, z * sku_backtest_rmse * sqrt((lead+review)/days_in_month))'},
        'sku_error_sigma':sigma,
        'model_relative_path':folder.relative_to(ROOT).as_posix(),
        'source_sql':DAILY_SQL,
        'live_source_note':'Input queried from MySQL; CSV files are model evaluation artifacts only, never a runtime sales fallback.'}

    store.execute('INSERT INTO Forecast_Run (run_id,metadata_json,predictions_json) VALUES (%s,%s,%s)',(run_id,json.dumps(metadata),json.dumps(records)))
    return dict(run_id=run_id,forecasts=records,metadata=metadata)
