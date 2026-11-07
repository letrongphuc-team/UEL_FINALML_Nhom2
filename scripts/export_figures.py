"""Export report-ready figures from the active run; no training or score changes."""
from pathlib import Path
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]

def export():
    folder=ROOT/json.loads((ROOT/'Models/active_plan_a.json').read_text())['path']
    metadata=json.loads((folder/'metadata.json').read_text())
    predictions=pd.read_csv(folder/'predictions.csv')
    scores=pd.read_csv(folder/'scores.csv')
    output=folder/'figures';output.mkdir(exist_ok=True)
    for frequency in ('monthly','weekly'):
        for target in ('quantity_sold','revenue_gbp'):
            choices=metadata['selections'][f'{frequency}:{target}']
            group=predictions[(predictions.frequency==frequency)&(predictions.target==target)&(predictions.horizon==1)]
            unit='GBP' if target=='revenue_gbp' else 'units'
            fig,ax=plt.subplots(figsize=(10,4.5),layout='constrained')
            actual=group[group.model==choices['baseline']].groupby('period').actual.sum()
            ax.plot(pd.to_datetime(actual.index),actual.values,label='Observed source total',color='#302822',marker='o')
            for role,color in [('baseline','#b76b35'),('champion','#287b82')]:
                forecast=group[group.model==choices[role]].groupby('period').forecast.sum()
                ax.plot(pd.to_datetime(forecast.index),forecast.values,label=role+': '+choices[role],color=color,linestyle='--',marker='.')
            ax.set(title=f'{frequency.title()} {target} — rolling backtest, horizon 1',xlabel='Target period start',ylabel=unit)
            ax.grid(alpha=.2);ax.legend(fontsize=8);fig.autofmt_xdate()
            fig.text(.01,.005,'Simulated HairCare labels / Online Retail II. Final test previously inspected. Run '+metadata['run_id'],fontsize=7)
            fig.savefig(output/f'{frequency}_{target}_backtest.png',dpi=180);plt.close(fig)
            rows=scores[(scores.frequency==frequency)&(scores.target==target)&(scores.horizon==1)&scores.model.isin(set(choices.values()))]
            pivot=rows.pivot(index='model',columns='partition',values='total_wape_pct')
            ax=pivot.plot.bar(figsize=(10,4.5),color=['#b76b35','#287b82'],rot=15)
            ax.set(title=f'{frequency.title()} total WAPE — horizon 1',xlabel='Candidate',ylabel='WAPE (%)')
            ax.grid(axis='y',alpha=.2);ax.figure.tight_layout();ax.figure.savefig(output/f'{frequency}_{target}_metrics.png',dpi=180);plt.close(ax.figure)
    print(output)

if __name__=='__main__':export()
