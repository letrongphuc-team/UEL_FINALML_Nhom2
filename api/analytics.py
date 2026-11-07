from datetime import timedelta
"""Historical source panel, kept separate from operational USD order reporting."""
import pandas as pd

def records(frame):
    return __import__('json').loads(frame.to_json(orient='records',date_format='iso'))

def historical_data(daily,source):
    from Models.research import panel
    frame=daily.copy()
    frame['sale_date']=pd.to_datetime(frame.sale_date)
    frame['product_id']=frame.product_id.astype(str)
    frame[['quantity_sold','revenue_gbp']]=frame[['quantity_sold','revenue_gbp']].astype(float)
    monthly=panel(frame,'monthly');weekly=panel(frame,'weekly')
    daily_totals=frame.groupby('sale_date',as_index=False)[['quantity_sold','revenue_gbp']].sum()
    # Statistical flags are descriptive, not evidence of promotion, error or causality.
    flags=pd.Series(False,index=daily_totals.index)
    for target in ('quantity_sold','revenue_gbp'):
        values=daily_totals[target];median=values.median();mad=(values-median).abs().median()
        z=.6745*(values-median)/mad if mad else values*0
        daily_totals[target+'_robust_z']=z
        flags=flags|(z.abs()>3.5)
    anomalies=daily_totals.loc[flags].copy()
    quality=frame.groupby('product_id',as_index=False).agg(product_name=('product_name','last'),category=('category','last'),
        observed_days=('sale_date','size'),zero_days=('quantity_sold',lambda s:int((s==0).sum())),
        quantity_mean=('quantity_sold','mean'),quantity_std=('quantity_sold','std'))
    quality['zero_day_pct']=quality.zero_days/quality.observed_days*100
    quality['quantity_cv']=quality.quantity_std/quality.quantity_mean.replace(0,float('nan'))
    return dict(source=source,is_simulated=True,currency='GBP',
        source_start=str(frame.sale_date.min().date()),source_end=str(frame.sale_date.max().date()),
        complete_month_end=str((monthly.period.max()+pd.offsets.MonthEnd(0)).date()),
        complete_week_end=str((weekly.period.max()+timedelta(days=6)).date()),
        monthly=records(monthly),weekly=records(weekly),quality=records(quality),anomalies=records(anomalies),
        note='Online Retail II quantities/GBP revenue with simulated HairCare labels. Complete periods only in monthly/weekly charts. Zero observed sales does not prove stockout. Category labels are mapped, not source HairCare demand.')

def from_store(store):
    rows=store.all('''SELECT s.sale_date,s.product_id,s.quantity_sold,s.revenue AS revenue_gbp,p.category,p.name AS product_name
        FROM Daily_Sales s JOIN Product p ON p.product_id=s.product_id WHERE s.is_simulated=1 ORDER BY s.product_id,s.sale_date''')
    if not rows:
        from api.business import BusinessError
        raise BusinessError('Historical Daily_Sales is empty. Import the prepared source panel first.',409)
    return historical_data(pd.DataFrame(rows),'MySQL Daily_Sales (historical source panel)')
