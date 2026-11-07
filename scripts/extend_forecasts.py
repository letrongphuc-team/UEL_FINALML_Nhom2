"""Export recursive 3-month/12-week projections with separate retrospective diagnostics.

Does not change model selection or existing benchmark results. Fits at historical
origins only for offline diagnostics; live inference never fits.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import hashlib,json,pickle
from datetime import datetime
import pandas as pd
from api.forecasting import active_folder
from api.analytics import historical_data,records
from Models.research import inference,panel,aggregate,features,estimator,predict,metrics,BASELINES,fit_forecaster

def extend(folder=None,daily=None,activate=True):
    folder=folder or active_folder()
    with (folder/'package.pkl').open('rb') as file:package=pickle.load(file)
    daily=daily.copy() if daily is not None else pd.read_csv(folder/'daily_snapshot.csv',dtype={'product_id':str},parse_dates=['sale_date'])
    run_id=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    output=folder/'extensions'/run_id;output.mkdir(parents=True,exist_ok=False)
    future=inference(package,daily,{'monthly':3,'weekly':12})
    future.to_csv(output/'forecasts.csv',index=False)
    history=historical_data(daily,'Packaged training snapshot / Online Retail II')
    (output/'history.json').write_text(json.dumps(history,ensure_ascii=False),encoding='utf-8')
    predictions=[]
    for frequency,maximum in [('monthly',3),('weekly',12)]:
        p=panel(daily,frequency);dates=sorted(p.period.unique())
        # Earlier origins let all requested horizons be examined before the published test.
        origins=dates[-9:-3] if frequency=='monthly' else dates[-24:-12:2]
        boundary=pd.Timestamp(package['metadata']['evaluation_origins'][frequency]['previously_inspected_test'])
        for target in ('quantity_sold','revenue_gbp'):
            selected=package['metadata']['selections'][frequency+':'+target]
            for candidate in sorted(set(selected.values())):
                algorithm,level,transform=candidate.split('|')
                dataset=aggregate(p) if level=='total' else p
                frame,columns=features(dataset,target,frequency)
                for origin in origins:
                    history_at_origin=p[p.period<origin]
                    model,columns=fit_forecaster(history_at_origin,target,frequency,candidate)
                    result=predict(p[p.period<origin],target,frequency,candidate,model,columns,maximum)
                    result=result[result.period<boundary]
                    result=result.merge(p[['product_id','period',target]],on=['product_id','period'],validate='one_to_one').rename(columns={target:'actual'})
                    result=result.assign(frequency=frequency,target=target,model=candidate,origin=pd.Timestamp(origin).isoformat(),partition='retrospective_diagnostic')
                    predictions.append(result)
    all_predictions=pd.concat(predictions,ignore_index=True)
    all_predictions.to_csv(output/'horizon_predictions.csv',index=False)
    scores=[]
    for keys,group in all_predictions.groupby(['frequency','target','model','horizon']):
        scores.append(dict(zip(['frequency','target','model','horizon'],keys),origins=int(group.origin.nunique()),
            partition='retrospective_diagnostic',**metrics(group)))
    pd.DataFrame(scores).to_csv(output/'horizon_scores.csv',index=False)
    metadata=dict(extension_id=run_id,model_run_id=package['metadata']['run_id'],
        snapshot_sha256=hashlib.sha256((folder/'daily_snapshot.csv').read_bytes()).hexdigest(),
        max_horizons={'monthly':3,'weekly':12},
        evaluation_note='Retrospective diagnostics of already selected model families, using earlier origins. Not new holdout evidence or a new selection experiment. All diagnostic targets precede the published test boundary. Errors are separated by horizon; no calibrated interval is claimed.',
        source_cutoffs=package['metadata']['complete_period_cutoffs'])
    (output/'metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    manifest=ROOT/'Models/active_analysis.json';temporary=manifest.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(model_run_id=package['metadata']['run_id'],path=output.relative_to(ROOT).as_posix()),indent=2),encoding='utf-8')
    if activate:
        temporary.replace(manifest)
    else:
        temporary.unlink()
    print(f'Extended forecasts and retrospective horizon diagnostics: {output}')
    return output

if __name__=='__main__':extend()
