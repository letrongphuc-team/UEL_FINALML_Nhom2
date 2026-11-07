"""Validation-selected coursework refinement with an explicit retrospective release gate.

Monthly candidates are Ridge with short lags or calendar features. The existing
weekly model is retained. SQL input is passed as a DataFrame by the caller.
"""
from pathlib import Path
from datetime import datetime,timedelta
import hashlib
import importlib.metadata
import json
import pickle
import time
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
REFERENCE=ROOT/'Models/results/plan_a_v2/20261107_215317_027878'
MONTHLY_CANDIDATES=('RidgeShort|total|raw','RidgeShort|total|log','CalendarRidge1|total|raw','CalendarRidge1|total|log')


def evaluation_origins(frame,frequency):
    dates=sorted(frame.period.unique())
    if frequency=='monthly':
        if len(dates)<24:raise ValueError('Coursework monthly comparison requires 24 complete periods')
        return dates[-9:-1],dates[-1]
    if len(dates)<80:raise ValueError('Coursework weekly comparison requires 80 complete periods')
    return [dates[i] for i in np.linspace(len(dates)-35,len(dates)-4,8,dtype=int)],dates[-2]


def _scored_prediction(panel,target,frequency,candidate,origin,horizon,partition):
    from Models import research as r
    history=panel[panel.period<origin]
    model,columns=r.fit_forecaster(history,target,frequency,candidate)
    future=r.predict(history,target,frequency,candidate,model,columns,horizon)
    future=future.merge(panel[['product_id','period',target]],on=['product_id','period'],validate='one_to_one').rename(columns={target:'actual'})
    return future.assign(frequency=frequency,target=target,model=candidate,origin=pd.Timestamp(origin).isoformat(),partition=partition)


def train(daily,source,activate=True):
    from Models import research as r
    from api.forecasting import database_fingerprint
    started=time.monotonic();run_id=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    folder=ROOT/'Models/results/plan_a_v3'/run_id;folder.mkdir(parents=True,exist_ok=False)
    reference=json.loads((REFERENCE/'metadata.json').read_text())
    canonical=daily.sort_values(['product_id','sale_date']).to_csv(index=False).encode();(folder/'daily_snapshot.csv').write_bytes(canonical)
    metadata=dict(run_id=run_id,source=source,profile='coursework-v3-monthly',reference_run=reference['run_id'],
        snapshot_sha256=hashlib.sha256(canonical).hexdigest(),source_sha256=database_fingerprint(daily),
        is_simulated=True,currency='GBP',data_cutoff=str(pd.to_datetime(daily.sale_date).max().date()),
        selection='Eight expanding validation origins; >=3% total WAPE gain and SKU WAPE degradation <=10%. Weekly family retained.',
        evaluation_note='The final test was previously inspected. It is used as a retrospective deployment acceptance check, not a new untouched holdout.',
        statistical_note='Eight validation origins and few test periods: descriptive results only; no statistical superiority or calibrated intervals claimed.',
        test_use={'candidate_selection':False,'deployment_acceptance':True,'scope':'Previously inspected retrospective backtest; no hyperparameter tuning on test'},
        configurations=dict(monthly_candidates=list(MONTHLY_CANDIDATES),weekly='Retain incumbent CatBoost family',seed=42),
        versions={p:importlib.metadata.version(p) for p in ('pandas','numpy','scikit-learn','lightgbm','catboost')},
        selections={},evaluation_origins={},complete_period_cutoffs={},improvement_comparison={})
    (folder/'experiment_config.json').write_text(json.dumps(metadata,indent=2))
    package=dict(metadata=metadata,models={},columns={},panels={});panels={};predictions=[];rankings={};proposals={};origins={}
    # Phase 1: validation only. No test targets are evaluated in this phase.
    for frequency in ('monthly','weekly'):
        p=r.panel(daily,frequency);panels[frequency]=p;package['panels'][frequency]=p
        validation,test=evaluation_origins(p,frequency);origins[frequency]=(validation,test)
        metadata['evaluation_origins'][frequency]={'validation':[pd.Timestamp(v).isoformat() for v in validation],'previously_inspected_test':pd.Timestamp(test).isoformat()}
        last=pd.Timestamp(p.period.max());metadata['complete_period_cutoffs'][frequency]=(last+pd.offsets.MonthEnd(0) if frequency=='monthly' else last+timedelta(days=6)).date().isoformat()
        horizon=1 if frequency=='monthly' else 2
        for target in r.TARGETS:
            key=frequency+':'+target;incumbent=reference['selections'][key]['champion']
            candidates=sorted(set([incumbent,'Rolling mean|total|raw']+(list(MONTHLY_CANDIDATES) if frequency=='monthly' else [])))
            ranking=[]
            for candidate in candidates:
                rows=[]
                for origin in validation:
                    result=_scored_prediction(p,target,frequency,candidate,origin,horizon,'validation')
                    if not (result.period<pd.Timestamp(test)).all():raise ValueError('Validation overlaps test')
                    rows.append(result)
                joined=pd.concat(rows,ignore_index=True);predictions.append(joined)
                ranking.append(dict(model=candidate,**r.metrics(joined)))
            ref=next(row for row in ranking if row['model']==incumbent)
            eligible=[row for row in ranking if row['model']!=incumbent and row['total_wape_pct']<=ref['total_wape_pct']*.97 and row['sku_wape_pct']<=ref['sku_wape_pct']*1.10] if frequency=='monthly' else []
            selected=min(eligible,key=lambda row:(row['total_wape_pct'],row['sku_wape_pct'],row['total_mae'])) if eligible else ref
            proposals[key]=dict(candidate=selected['model'],reference=incumbent,validation_candidate=selected,validation_reference=ref)
            rankings[key]=ranking
    (folder/'selections_frozen_before_test.json').write_text(json.dumps(proposals,indent=2))
    metadata['selection_frozen_sha256']=hashlib.sha256((folder/'selections_frozen_before_test.json').read_bytes()).hexdigest()
    # Phase 2: report the already inspected test and apply the disclosed release gate.
    for frequency in ('monthly','weekly'):
        p=panels[frequency];_,test=origins[frequency];horizon=1 if frequency=='monthly' else 2
        for target in r.TARGETS:
            key=frequency+':'+target;proposal=proposals[key];candidate=proposal['candidate'];incumbent=proposal['reference']
            selected_models=sorted(set([candidate,incumbent,'Rolling mean|total|raw']))
            test_frames={}
            for name in selected_models:
                frame=_scored_prediction(p,target,frequency,name,test,horizon,'test');test_frames[name]=frame;predictions.append(frame)
            before=r.metrics(test_frames[incumbent]);challenger=r.metrics(test_frames[candidate])
            gate=all(challenger[name]<=before[name]+1e-8 for name in ('total_wape_pct','sku_wape_pct','total_mae','total_rmse'))
            promoted=frequency=='monthly' and candidate!=incumbent and gate
            champion=candidate if promoted else incumbent
            after=r.metrics(test_frames[champion])
            metadata['selections'][key]=dict(champion=champion,best_ml=champion,baseline='Rolling mean|total|raw')
            metadata['improvement_comparison'][key]=dict(reference_model=incumbent,selected_model=champion,candidate_model=candidate,
                promoted=promoted,validation_before=proposal['validation_reference'],validation_after=next(row for row in rankings[key] if row['model']==champion),
                test_before=before,test_after=after,test_candidate=challenger,
                test_by_horizon=[dict(horizon=int(h),before=r.metrics(test_frames[incumbent][test_frames[incumbent].horizon==h]),after=r.metrics(test_frames[champion][test_frames[champion].horizon==h])) for h in sorted(test_frames[champion].horizon.unique())],
                note='Retrospective comparison; test is not an untouched holdout. Weekly model family was retained.' if frequency=='weekly' else 'Candidate selected on validation; previously inspected test used only for the disclosed release acceptance check.')
            for name in set(metadata['selections'][key].values()):
                model,columns=r.fit_forecaster(p,target,frequency,name);package['models'][(key,name)]=model;package['columns'][(key,name)]=columns
    frame=pd.concat(predictions,ignore_index=True);frame.to_csv(folder/'predictions.csv',index=False)
    scores=[];per_origin=[]
    for keys,group in frame.groupby(['frequency','target','model','partition','horizon']):scores.append(dict(zip(['frequency','target','model','partition','horizon'],keys),**r.metrics(group)))
    for keys,group in frame.groupby(['frequency','target','model','origin','horizon','partition']):per_origin.append(dict(zip(['frequency','target','model','origin','horizon','partition'],keys),**r.metrics(group)))
    pd.DataFrame(scores).to_csv(folder/'scores.csv',index=False);pd.DataFrame(per_origin).to_csv(folder/'origin_scores.csv',index=False)
    metadata['scores']=json.loads(pd.DataFrame(scores).to_json(orient='records'))
    metadata['canonical_reconciliation']=dict(quantity=float(daily.quantity_sold.sum()),revenue_gbp=float(daily.revenue_gbp.sum()))
    metadata['training_seconds']=time.monotonic()-started
    (folder/'metadata.json').write_text(json.dumps(metadata,indent=2))
    with (folder/'package.pkl').open('wb') as file:pickle.dump(package,file)
    future=r.inference(package,daily);future.to_csv(folder/'forecasts.csv',index=False)
    with (folder/'package.pkl').open('rb') as file:restored=pickle.load(file)
    if not np.allclose(future.forecast,r.inference(restored,daily).forecast):raise RuntimeError('Model round-trip mismatch')
    from scripts.analyze_run import analyze
    analyze(folder,reference_daily=daily)
    if activate:
        manifest=ROOT/'Models/active_plan_a.json';temporary=manifest.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(run_id=run_id,path=folder.relative_to(ROOT).as_posix()),indent=2));temporary.replace(manifest)
    return folder
