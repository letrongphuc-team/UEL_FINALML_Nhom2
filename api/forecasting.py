from pathlib import Path
from datetime import datetime,timedelta
from uuid import uuid4
import json
import hashlib
import pickle
import pandas as pd
from api.business import BusinessError,recommended_order

ROOT=Path(__file__).resolve().parents[1]

def active_folder():
    path=ROOT/'Models/active_plan_a.json'
    if not path.exists(): raise BusinessError('No validated Plan A package. Run Models/research.py first.',409)
    entry=json.loads(path.read_text(encoding='utf-8'))
    folder=(ROOT/entry['path']).resolve()
    if not folder.is_relative_to((ROOT/'Models/results').resolve()): raise BusinessError('Invalid model manifest',409)
    for name in ('package.pkl','metadata.json','scores.csv','origin_scores.csv','forecasts.csv','predictions.csv'):
        if not (folder/name).is_file(): raise BusinessError(f'Model run is incomplete: missing {name}',409)
    return folder

def report(store=None):
    folder=active_folder()
    result={'metadata':json.loads((folder/'metadata.json').read_text(encoding='utf-8'))}
    for name in ('scores','origin_scores','forecasts'):
        frame=pd.read_csv(folder/f'{name}.csv')
        result[name]=json.loads(frame.to_json(orient='records'))
    actual=pd.read_csv(folder/'predictions.csv')
    grouped=actual.groupby(['frequency','target','model','partition','origin','period','horizon'],as_index=False)[['actual','forecast']].sum()
    result['backtest']=json.loads(grouped.to_json(orient='records'))
    extension=analysis_folder()
    if extension:
        result['forecasts']=json.loads(pd.read_csv(extension/'forecasts.csv').to_json(orient='records'))
        result['horizon_scores']=json.loads(pd.read_csv(extension/'horizon_scores.csv').to_json(orient='records'))
        result['extension']=json.loads((extension/'metadata.json').read_text(encoding='utf-8'))
    if store:
        latest_key=store.one('SELECT run_id FROM Forecast_Run ORDER BY created_at DESC,run_id DESC LIMIT 1')
        latest=store.one('SELECT * FROM Forecast_Run WHERE run_id=%s',(latest_key['run_id'],)) if latest_key else None
        if latest:
            metadata=json.loads(latest['metadata_json'])
            if metadata['run_id']==result['metadata']['run_id'] and metadata.get('projection_horizons')=={'monthly':3,'weekly':12}:
                result['forecasts']=json.loads(latest['predictions_json'])
                result['inference_run_id']=latest['run_id']
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
    canonical=daily.sort_values(['product_id','sale_date']).to_csv(index=False,date_format='%Y-%m-%d',float_format='%.8f')
    return hashlib.sha256(canonical.encode()).hexdigest()


def run_inference(store,folder=None):
    from Models.research import inference
    folder=folder or active_folder()
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
    try:
        frame=inference(package,daily,{'monthly':3,'weekly':12})
    except (ValueError,KeyError,TypeError) as error:
        raise BusinessError(f'Model input is incompatible: {error}',409) from error
    run_id='inference_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8]
    frame['inference_run_id']=run_id
    settings={r['setting_key']:r['setting_value'] for r in store.all('SELECT * FROM Settings')}
    lead=int(float(settings.get('lead_time_days',7))); review=int(float(settings.get('review_period_days',30))); safety=float(settings.get('safety_stock',15))
    for (frequency,role,product,period),group in frame.groupby(['frequency','role','product_id','period']):
        variant=store.one('SELECT * FROM Product_Variant WHERE product_id=%s ORDER BY variant_id LIMIT 1',(str(product),))
        if not variant: raise BusinessError('Forecast product missing from catalog',409)
        start=pd.Timestamp(period); end=start+pd.offsets.MonthEnd(0) if frequency=='monthly' else start+timedelta(days=6)
        days=(end-start).days+1
        quantity=float(group.loc[group.target=='quantity_sold','forecast'].iloc[0]); revenue=float(group.loc[group.target=='revenue_gbp','forecast'].iloc[0])
        name='; '.join(group.model.unique())[:100]
        fid=store.execute('''INSERT INTO Inventory_Forecast (product_id,variant_id,forecast_date,period_end,horizon_days,forecast_quantity,
            forecast_revenue,revenue_currency,model_name,run_id,target_role,frequency,data_cutoff) VALUES (%s,%s,%s,%s,%s,%s,%s,'GBP',%s,%s,%s,%s,%s)''',
            (str(product),variant['variant_id'],start.date(),end.date(),days,quantity,revenue,name,run_id,role,frequency,str(daily.sale_date.max().date())))
        if frequency=='monthly' and role=='champion' and start==frame.loc[frame.frequency=='monthly','period'].min():
            demand=quantity*(lead+review)/days
            recommended=recommended_order(demand,safety,variant['on_hand'],variant['reserved'],variant['incoming'])
            store.execute('''INSERT INTO Inventory_Recommendation (forecast_id,product_id,variant_id,current_stock,safety_stock,recommended_order_quantity,lead_time_days,review_period_days)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',(fid,str(product),variant['variant_id'],max(0,variant['on_hand']-variant['reserved']),safety,recommended,lead,review))
    records=json.loads(frame.to_json(orient='records',date_format='iso'))
    metadata={**package['metadata'],'projection_horizons':{'monthly':3,'weekly':12},
        'source':'mysql:Daily_Sales','data_cutoff':str(daily.sale_date.max().date()),
        'source_sha256':database_fingerprint(daily),
        'database':store.one('SELECT DATABASE() AS name')['name'],
        'source_rows':len(daily),'source_products':int(daily.product_id.nunique()),
        'scores':scores,
        'model_relative_path':folder.relative_to(ROOT).as_posix(),
        'source_sql':DAILY_SQL,
        'live_source_note':'Input queried from MySQL; CSV files are model evaluation artifacts only, never a runtime sales fallback.'}

    store.execute('INSERT INTO Forecast_Run (run_id,metadata_json,predictions_json) VALUES (%s,%s,%s)',(run_id,json.dumps(metadata),json.dumps(records)))
    return dict(run_id=run_id,forecasts=records,metadata=metadata)
