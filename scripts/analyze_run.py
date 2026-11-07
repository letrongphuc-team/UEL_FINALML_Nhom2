"""Export paired baseline comparisons, seasonal errors and rank stability without fitting."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import json
import pandas as pd
from Models.research import metrics

def analyze(folder,reference_daily=None):
    scores=pd.read_csv(folder/'origin_scores.csv')
    keys=['frequency','target','origin','horizon','partition']
    baseline=scores.loc[scores.model=='Rolling mean|total|raw',keys+['total_mae','total_wape_pct']]
    paired=scores.merge(baseline,on=keys,suffixes=('','_baseline'),validate='many_to_one')
    paired['absolute_error_improvement']=paired.total_mae_baseline-paired.total_mae
    paired['wape_improvement_points']=paired.total_wape_pct_baseline-paired.total_wape_pct
    paired['rank_within_origin']=paired.groupby(keys).total_wape_pct.rank(method='min')
    paired.to_csv(folder/'paired_comparisons.csv',index=False)
    predictions=pd.read_csv(folder/'predictions.csv')
    predictions['calendar_month']=pd.to_datetime(predictions.period).dt.month
    diagnostics=[]
    for keys,group in predictions.groupby(['frequency','target','model','partition','calendar_month']):
        diagnostics.append(dict(zip(['frequency','target','model','partition','calendar_month'],keys),**metrics(group)))
    pd.DataFrame(diagnostics).to_csv(folder/'seasonal_diagnostics.csv',index=False)
    sku=predictions.assign(error=predictions.forecast-predictions.actual)
    sku=sku.groupby(['frequency','target','model','partition','product_id','product_name'],as_index=False).agg(bias=('error','mean'),total_actual=('actual','sum'),total_forecast=('forecast','sum'))
    sku.to_csv(folder/'sku_error_diagnostics.csv',index=False)
    metadata=json.loads((folder/'metadata.json').read_text())
    canonical=reference_daily.copy() if reference_daily is not None else pd.read_csv(ROOT/'data/processed_data_demo/haircare_demo_daily_sales.csv',dtype={'product_id':str})
    snapshot=pd.read_csv(folder/'daily_snapshot.csv',dtype={'product_id':str})
    for panel_frame in (canonical,snapshot):
        panel_frame['sale_date']=pd.to_datetime(panel_frame.sale_date).dt.strftime('%Y-%m-%d')
        panel_frame['product_id']=panel_frame.product_id.astype(str)
    left=canonical.groupby(['sale_date','product_id'])[['quantity_sold','revenue_gbp']].sum().sort_index()
    right=snapshot.groupby(['sale_date','product_id'])[['quantity_sold','revenue_gbp']].sum().sort_index()
    import numpy as np
    metadata['canonical_reconciliation']['all_product_dates_match']=bool(left.index.equals(right.index) and np.allclose(left,right))
    if not metadata['canonical_reconciliation']['all_product_dates_match']: raise ValueError('MySQL does not reconcile to canonical panel')
    (folder/'metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    notes=['# Forecasting model evaluation','',f'Run: {metadata["run_id"]}',
        '',metadata['evaluation_note'],'',metadata['statistical_note'],
        '', f"{len(metadata['evaluation_origins']['monthly']['validation'])} validation origins select candidates. {metadata.get('evaluation_note','Final test is reported separately.')}",
        '', 'Monthly and weekly WAPE use different aggregation periods. Total WAPE can hide SKU cancellation errors.',
        '', 'No December 2011 full-month actual is available. The 1–9 December partial month is excluded from monthly evaluation.',
        '', 'Weekly validation horizons do not overlap the reported weekly test period.',
        '', '## Selected models', '']
    overall=pd.read_csv(folder/'scores.csv')
    for key,choices in metadata['selections'].items():
        freq,target=key.split(':')
        notes.append(f'### {freq} / {target}')
        for role,model in choices.items():
            selected=overall[(overall.frequency==freq)&(overall.target==target)&(overall.model==model)]
            notes.append(f'- {role}: {model}')
            for _,row in selected.iterrows(): notes.append(f'  - {row.partition}, horizon {row.horizon}: total WAPE {row.total_wape_pct:.2f}%; SKU WAPE {row.sku_wape_pct:.2f}%; MAE {row.total_mae:.2f}.')
        notes.append('')
    notes+=['## Files','', 'scores.csv: target/partition/horizon metrics; origin_scores.csv: fold detail; paired_comparisons.csv: paired error deltas and ranks; seasonal_diagnostics.csv: month diagnostics; sku_error_diagnostics.csv: SKU bias; timings.csv: fit/inference runtime.',
        '', metadata.get('statistical_note','Descriptive forecasting diagnostics; no claim of causal seasonality or real HairCare demand accuracy.')]
    (folder/'REPORT.md').write_text('\n'.join(notes),encoding='utf-8')

if __name__=='__main__':
    manifest=json.loads((ROOT/'Models/active_plan_a.json').read_text())
    folder=ROOT/manifest['path'];analyze(folder);print(folder/'REPORT.md')
