"""Reproducible Plan A benchmark and inference. Existing result folders are immutable.

Default input is MySQL. --csv is an explicit reproducibility option, never a fallback.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import argparse
import hashlib
import importlib.metadata
import json
import pickle
import time
import warnings
from datetime import datetime,timedelta
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer,TransformedTargetRegressor
from sklearn.preprocessing import OneHotEncoder,StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.linear_model import LinearRegression,Ridge
from sklearn.ensemble import ExtraTreesRegressor
from lightgbm import LGBMRegressor
from catboost import CatBoostRegressor
warnings.filterwarnings('ignore',message='X does not have valid feature names, but LGBMRegressor was fitted with feature names',category=UserWarning)

TARGETS=('quantity_sold','revenue_gbp')
BASELINES=('Lag 1','Rolling mean','Seasonal naive')
ALGORITHMS=('Linear','Ridge','ExtraTrees','LightGBM','CatBoost')

def load_daily(csv=None):
    if csv:
        return pd.read_csv(csv,dtype={'product_id':str},parse_dates=['sale_date'])
    from connector.connector import Connector
    db=Connector()
    try:
        db.connect()
        frame=db.queryDataset('''SELECT s.sale_date,s.product_id,s.quantity_sold,s.revenue AS revenue_gbp,p.category,p.name AS product_name
            FROM Daily_Sales s JOIN Product p ON p.product_id=s.product_id WHERE s.is_simulated=1 ORDER BY s.product_id,s.sale_date''')
        frame['sale_date']=pd.to_datetime(frame.sale_date)
        frame['product_id']=frame.product_id.astype(str)
        frame[list(TARGETS)]=frame[list(TARGETS)].astype(float)
        return frame
    finally: db.close()

def panel(daily,frequency):
    frame=daily.copy()
    frame['sale_date']=pd.to_datetime(frame.sale_date)
    frame['product_id']=frame.product_id.astype(str)
    last=frame.sale_date.max().normalize()
    first=frame.sale_date.min().normalize()
    if frequency=='monthly':
        end=last.to_period('M')
        if last.day!=last.days_in_month: end-=1
        begin=first.to_period('M')+(1 if first.day!=1 else 0)
        periods=pd.period_range(begin,end,freq='M')
        frame['period']=frame.sale_date.dt.to_period('M').dt.to_timestamp()
        dates=periods.to_timestamp()
    else:
        end=last-timedelta(days=(last.weekday()+1)%7)
        begin=first+timedelta(days=(7-first.weekday())%7)
        frame=frame.loc[frame.sale_date.between(begin,end)].copy()
        frame['period']=frame.sale_date.dt.to_period('W-SUN').dt.start_time
        dates=pd.date_range(begin,end-timedelta(days=6),freq='W-MON')
    frame=frame.loc[frame.period.isin(dates)]
    result=frame.groupby(['product_id','period'],as_index=False)[list(TARGETS)].sum()
    ids=sorted(daily.product_id.astype(str).unique())
    index=pd.MultiIndex.from_product([ids,dates],names=['product_id','period'])
    result=result.set_index(['product_id','period']).reindex(index,fill_value=0).reset_index()
    metadata=daily.assign(product_id=daily.product_id.astype(str)).groupby('product_id')[['category','product_name']].last()
    return result.join(metadata,on='product_id').sort_values(['product_id','period']).reset_index(drop=True)

def spec(frequency):
    return ((1,2,3,6,12),(3,6),12) if frequency=='monthly' else ((1,2,4,8,13),(4,8),52)

def features(frame,target,frequency):
    result=frame.sort_values(['product_id','period']).copy()
    grouped=result.groupby('product_id')[target]
    lags,windows,_=spec(frequency)
    names=[]
    for lag in lags:
        name=f'lag_{lag}'; names.append(name); result[name]=grouped.shift(lag)
    for window in windows:
        name=f'mean_{window}'; names.append(name)
        result[name]=grouped.transform(lambda s:s.shift().rolling(window).mean())
    cycle=result.period.dt.month if frequency=='monthly' else result.period.dt.isocalendar().week.astype(int)
    length=12 if frequency=='monthly' else 52
    result['sin']=np.sin(2*np.pi*cycle/length); result['cos']=np.cos(2*np.pi*cycle/length)
    names+=['sin','cos']
    return result,['product_id','category']+names

def estimator(algorithm,columns,transform):
    numeric=[x for x in columns if x not in ('product_id','category')]
    pre=ColumnTransformer([('cat',OneHotEncoder(handle_unknown='ignore',sparse_output=False),['product_id','category']),
                          ('num',StandardScaler(),numeric)])
    models={'Linear':lambda:LinearRegression(),'Ridge':lambda:Ridge(alpha=10),
        'ExtraTrees':lambda:ExtraTreesRegressor(n_estimators=80,min_samples_leaf=3,random_state=42,n_jobs=2),
        'LightGBM':lambda:LGBMRegressor(n_estimators=80,num_leaves=7,min_child_samples=8,verbosity=-1,random_state=42,n_jobs=2),
        'CatBoost':lambda:CatBoostRegressor(iterations=100,depth=4,verbose=False,random_seed=42,thread_count=2,allow_writing_files=False)}
    model=make_pipeline(pre,models[algorithm]())
    return TransformedTargetRegressor(regressor=model,func=np.log1p,inverse_func=np.expm1) if transform=='log' else model

def aggregate(frame):
    return frame.groupby('period',as_index=False)[list(TARGETS)].sum().assign(product_id='TOTAL',category='all',product_name='Total panel')

def fit_forecaster(history,target,frequency,candidate):
    algorithm,level,transform=candidate.split('|')
    if algorithm in ('RidgeShort','CalendarRidge1'):
        from Models.coursework_models import fit_model
        return fit_model(history,target,frequency,candidate)
    dataset=aggregate(history) if level=='total' else history
    frame,columns=features(dataset,target,frequency)
    training=frame.dropna(subset=columns+[target])
    model=None
    if algorithm not in BASELINES:
        model=estimator(algorithm,columns,transform);model.fit(training[columns],training[target])
    return model,columns


def predict(history,target,frequency,candidate,model,columns,horizon):
    if candidate.split('|')[0] in ('RidgeShort','CalendarRidge1'):
        from Models.coursework_models import predict as coursework_predict
        return coursework_predict(history,target,frequency,candidate,model,columns,horizon)
    algorithm,level,transform=candidate.split('|')
    working=aggregate(history) if level=='total' else history.copy()
    step=pd.offsets.MonthBegin(1) if frequency=='monthly' else timedelta(days=7)
    window=3 if frequency=='monthly' else 4
    shares=history.sort_values('period').groupby('product_id').tail(window).groupby('product_id')[target].sum()
    shares=shares/shares.sum() if shares.sum()>0 else shares*0+1/len(shares)
    metadata=history.groupby('product_id')[['product_name','category']].last()
    output=[]
    for h in range(1,horizon+1):
        period=working.period.max()+step
        future=working.groupby('product_id')[['category','product_name']].last().reset_index().assign(period=period)
        future[target]=np.nan
        combined=pd.concat([working,future],ignore_index=True)
        enriched,_=features(combined,target,frequency)
        rows=enriched.loc[enriched.period==period].copy()
        if algorithm in BASELINES:
            lag=1 if algorithm=='Lag 1' else spec(frequency)[2]
            if algorithm=='Rolling mean':
                values=working.groupby('product_id')[target].apply(lambda s:s.tail(window).mean()).reindex(rows.product_id).to_numpy()
            else:
                values=working.loc[working.period==period-(pd.DateOffset(months=lag) if frequency=='monthly' else timedelta(weeks=lag))].set_index('product_id')[target].reindex(rows.product_id).to_numpy()
        else: values=model.predict(rows[columns])
        values=np.maximum(np.asarray(values,dtype=float),0)
        if not np.isfinite(values).all(): raise ValueError(f'Incomplete history for {candidate}')
        future[target]=values
        working=pd.concat([working,future],ignore_index=True)
        if level=='total':
            result=metadata.reset_index().assign(forecast=shares.reindex(metadata.index).to_numpy()*values.sum())
        else:
            result=rows[['product_id','product_name','category']].copy(); result['forecast']=values
        result['period']=period; result['horizon']=h
        output.append(result)
    return pd.concat(output,ignore_index=True)

def metrics(group):
    def errors(a,p):
        error=p-a; denominator=np.abs(a).sum()
        return dict(wape_pct=float(np.abs(error).sum()/denominator*100) if denominator else None,
                    mae=float(np.abs(error).mean()),rmse=float(np.sqrt(np.mean(error**2))),bias=float(error.mean()))
    total=group.groupby(['origin','period','horizon'])[['actual','forecast']].sum()
    return {**{'total_'+k:v for k,v in errors(total.actual.to_numpy(),total.forecast.to_numpy()).items()},
            **{'sku_'+k:v for k,v in errors(group.actual.to_numpy(),group.forecast.to_numpy()).items()}}

def train_legacy(daily,source,activate=True):
    run_id=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    folder=ROOT/'Models/results/plan_a_v2'/run_id
    folder.mkdir(parents=True,exist_ok=False)
    canonical=daily.sort_values(['product_id','sale_date']).to_csv(index=False).encode()
    (folder/'daily_snapshot.csv').write_bytes(canonical)
    metadata=dict(run_id=run_id,source=source,snapshot_sha256=hashlib.sha256(canonical).hexdigest(),
        is_simulated=True,currency='GBP',evaluation_note='November 2011 is a previously inspected test period, not an untouched holdout.',
        selection='Validation pooled total WAPE, then total MAE, then model simplicity',
        versions={p:importlib.metadata.version(p) for p in ('pandas','numpy','scikit-learn','lightgbm','catboost')},
        configurations=dict(algorithms=list(ALGORITHMS),levels=['total','sku'],transforms=['raw','log'],seed=42),selections={},evaluation_origins={},complete_period_cutoffs={})
    # Freeze configuration before running any experiments.
    (folder/'experiment_config.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    package=dict(metadata=metadata,models={},columns={},panels={})
    predictions=[]; timings=[]
    for frequency in ('monthly','weekly'):
        p=panel(daily,frequency); package['panels'][frequency]=p
        dates=sorted(p.period.unique()); origins=dates[-6:]
        if len(dates)<18: raise ValueError('Insufficient complete periods')
        # Both weekly horizons must have fully observed actuals at every origin.
        if frequency=='weekly': origins=[*dates[-8:-3],dates[-2]]
        metadata['evaluation_origins'][frequency]={'validation':[pd.Timestamp(d).isoformat() for d in origins[:-1]],'previously_inspected_test':pd.Timestamp(origins[-1]).isoformat()}
        last=pd.Timestamp(dates[-1])
        metadata['complete_period_cutoffs'][frequency]=(last+pd.offsets.MonthEnd(0) if frequency=='monthly' else last+timedelta(days=6)).date().isoformat()
        for target in TARGETS:
            candidates=[f'{b}|total|raw' for b in BASELINES if frequency=='monthly' or b!='Seasonal naive']
            candidates += [f'{a}|{level}|{tr}' for a in ALGORITHMS for level in ('total','sku') for tr in ('raw','log')]
            for candidate in candidates:
                algorithm,level,tr=candidate.split('|')
                dataset=aggregate(p) if level=='total' else p
                frame,columns=features(dataset,target,frequency)
                for origin in origins:
                    history=p.loc[p.period<origin]
                    training=frame.loc[frame.period<origin].dropna(subset=columns+[target])
                    model=None; started=time.perf_counter()
                    if algorithm not in BASELINES:
                        model=estimator(algorithm,columns,tr); model.fit(training[columns],training[target])
                    fit_seconds=time.perf_counter()-started; started=time.perf_counter()
                    forecast=predict(history,target,frequency,candidate,model,columns,1 if frequency=='monthly' else 2)
                    timings.append(dict(frequency=frequency,target=target,model=candidate,origin=str(origin),fit_seconds=fit_seconds,inference_seconds=time.perf_counter()-started))
                    forecast=forecast.merge(p[['product_id','period',target]],on=['product_id','period'],validate='one_to_one').rename(columns={target:'actual'})
                    forecast=forecast.assign(frequency=frequency,target=target,model=candidate,origin=pd.Timestamp(origin).isoformat(),partition='test' if origin==origins[-1] else 'validation')
                    predictions.append(forecast)
            joined=pd.concat(predictions,ignore_index=True)
            subset=joined.loc[(joined.frequency==frequency)&(joined.target==target)&(joined.partition=='validation')]
            ranking=[]
            for candidate,group in subset.groupby('model'):
                ranking.append(dict(model=candidate,**metrics(group)))
            ranking=sorted(ranking,key=lambda r:(r['total_wape_pct'],r['total_mae'],0 if r['model'].split('|')[0] in BASELINES else 1,r['model']))
            choices=dict(champion=ranking[0]['model'],best_ml=next(r['model'] for r in ranking if r['model'].split('|')[0] not in BASELINES),baseline='Rolling mean|total|raw')
            key=f'{frequency}:{target}'; metadata['selections'][key]=choices
            for candidate in set(choices.values()):
                algorithm,level,tr=candidate.split('|')
                frame,columns=features(aggregate(p) if level=='total' else p,target,frequency)
                training=frame.dropna(subset=columns+[target]); model=None
                if algorithm not in BASELINES:
                    model=estimator(algorithm,columns,tr); model.fit(training[columns],training[target])
                package['models'][(key,candidate)]=model; package['columns'][(key,candidate)]=columns
    all_predictions=pd.concat(predictions,ignore_index=True)
    all_predictions.to_csv(folder/'predictions.csv',index=False)
    scores=[]
    for keys,group in all_predictions.groupby(['frequency','target','model','partition','horizon']):
        scores.append(dict(zip(['frequency','target','model','partition','horizon'],keys),**metrics(group)))
    pd.DataFrame(scores).to_csv(folder/'scores.csv',index=False)
    metadata['scores']=json.loads(pd.DataFrame(scores).to_json(orient='records'))
    pd.DataFrame(timings).to_csv(folder/'timings.csv',index=False)
    per_origin=[]
    for keys,group in all_predictions.groupby(['frequency','target','model','origin','horizon','partition']):
        per_origin.append(dict(zip(['frequency','target','model','origin','horizon','partition'],keys),**metrics(group)))
    pd.DataFrame(per_origin).to_csv(folder/'origin_scores.csv',index=False)
    metadata['statistical_note']='Only five validation origins: descriptive paired errors are reported. No claim of statistical superiority or calibrated intervals.'
    metadata['data_cutoff']=str(pd.to_datetime(daily.sale_date).max().date())
    metadata['canonical_reconciliation']=dict(quantity=float(daily.quantity_sold.sum()),revenue_gbp=float(daily.revenue_gbp.sum()))
    (folder/'metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    with (folder/'package.pkl').open('wb') as f: pickle.dump(package,f)
    future=inference(package,daily)
    future.to_csv(folder/'forecasts.csv',index=False)
    with (folder/'package.pkl').open('rb') as f: restored=pickle.load(f)
    if not np.allclose(future.forecast,inference(restored,daily).forecast): raise RuntimeError('Package inference mismatch')
    from scripts.analyze_run import analyze
    analyze(folder,reference_daily=daily)
    # Active manifest is updated only after all output and round-trip checks pass.
    manifest=ROOT/'Models/active_plan_a.json'
    temporary=manifest.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(run_id=run_id,path=str(folder.relative_to(ROOT)).replace('\\','/')),indent=2),encoding='utf-8')
    if activate:
        temporary.replace(manifest)
    else:
        temporary.unlink()
    return folder

def inference(package,daily,horizons=None):
    horizons=horizons or {'monthly':1,'weekly':2}
    for frequency,maximum in [('monthly',3),('weekly',12)]:
        value=horizons.get(frequency,1 if frequency=='monthly' else 2)
        if isinstance(value,bool) or not isinstance(value,int) or not 1<=value<=maximum:
            raise ValueError(f'{frequency} horizon must be an integer from 1 to {maximum}')
    output=[]
    for key,choices in package['metadata']['selections'].items():
        frequency,target=key.split(':'); history=panel(daily,frequency)
        trained=package['panels'][frequency].period.max()
        if history.period.max()<trained: raise ValueError('Input ends before packaged training cutoff')
        if set(history.product_id)!=set(package['panels'][frequency].product_id): raise ValueError('Product universe differs from package; retraining required')
        for role,candidate in choices.items():
            count=horizons.get(frequency,1 if frequency=='monthly' else 2)
            frame=predict(history,target,frequency,candidate,package['models'][(key,candidate)],package['columns'][(key,candidate)],count)
            frame=frame.assign(frequency=frequency,target=target,role=role,model=candidate,run_id=package['metadata']['run_id'],data_cutoff=str(pd.to_datetime(daily.sale_date).max().date()),currency='GBP' if target=='revenue_gbp' else 'units',is_simulated=True)
            output.append(frame)
    return pd.concat(output,ignore_index=True)

def train(daily,source,activate=True):
    from Models.coursework_training import train as train_coursework
    return train_coursework(daily,source,activate=activate)


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--csv'); args=parser.parse_args()
    daily=load_daily(args.csv)
    print(train(daily,'csv:'+args.csv if args.csv else 'mysql:Daily_Sales'))
