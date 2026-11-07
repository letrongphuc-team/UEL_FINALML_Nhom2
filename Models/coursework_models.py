"""Coursework v3: short-history Ridge and calendar/trend Ridge.

Only known dates and lagged targets are features. Sales input is supplied by the
MySQL caller; this module has no file/database data-loading fallback.
"""
from datetime import timedelta
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator,RegressorMixin
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder,StandardScaler
from sklearn.linear_model import Ridge

NEW_ALGORITHMS=('RidgeShort','CalendarRidge1')

def raw_short_features(frame,target,frequency):
    out=frame.sort_values(['product_id','period']).copy()
    group=out.groupby('product_id')[target]
    window=3 if frequency=='monthly' else 4
    season=12 if frequency=='monthly' else 52
    mean=group.transform(lambda s:s.shift(1).rolling(window,min_periods=1).mean())
    long=group.transform(lambda s:s.shift(1).rolling(window*2,min_periods=1).mean())
    out['scale']=mean.clip(lower=1.)
    out.loc[out.groupby('product_id').cumcount()<window,'scale']=np.nan
    out['annual_available']=group.shift(season).notna().astype(float)
    out['relative_trend']=(mean/long.clip(lower=1.)).clip(0,4).fillna(1)
    out['zero_share']=group.transform(lambda s:s.shift(1).rolling(window,min_periods=1).apply(lambda a:np.mean(a==0),raw=True))
    out['age_years']=out.groupby('product_id').cumcount()/season
    cycle=out.period.dt.month if frequency=='monthly' else out.period.dt.isocalendar().week.astype(float)
    length=12 if frequency=='monthly' else 52
    out['calendar_sin']=np.sin(2*np.pi*cycle/length);out['calendar_cos']=np.cos(2*np.pi*cycle/length)
    columns=['product_id','category']
    for lag in ((1,2,3) if frequency=='monthly' else (1,2,4,8)):
        name=f'short_lag_{lag}';out[name]=group.shift(lag).fillna(0);columns.append(name)
    for size in (window,window*2):
        name=f'short_mean_{size}';out[name]=group.transform(lambda s:s.shift(1).rolling(size,min_periods=1).mean());columns.append(name)
    out['short_std']=group.transform(lambda s:s.shift(1).rolling(window,min_periods=2).std()).fillna(0)
    out['annual_value']=group.shift(season).fillna(0)
    columns+=['short_std','annual_value','annual_available','relative_trend','zero_share','age_years','calendar_sin','calendar_cos']
    for month in range(1,13):
        name=f'month_{month}';out[name]=(out.period.dt.month==month).astype(float);columns.append(name)
    if frequency=='monthly':out['period_days']=out.period.dt.days_in_month;columns.append('period_days')
    return out,columns

class ShortRegressor(RegressorMixin,BaseEstimator):
    """Ordinary regression with short lag features; no annual-history row deletion."""
    def __init__(self,algorithm='CatBoostShort',columns=None,transform='raw'):
        self.algorithm=algorithm;self.columns=columns;self.transform=transform
    def fit(self,X,y):
        self.columns_=list(self.columns)
        numeric=[c for c in self.columns_ if c not in ('product_id','category')]
        self.pre_=ColumnTransformer([('cat',OneHotEncoder(handle_unknown='ignore',sparse_output=False),['product_id','category']),('num',StandardScaler(),numeric)])
        design=self.pre_.fit_transform(X[self.columns_])
        self.reg_=Ridge(alpha=100)
        target=np.log1p(y) if self.transform=='log' else y
        self.reg_.fit(design,target)
        return self
    def predict(self,X):
        value=self.reg_.predict(self.pre_.transform(X[self.columns_]))
        return np.maximum(0,np.expm1(value) if self.transform=='log' else value)


class CalendarRegressor(RegressorMixin,BaseEstimator):
    """Ridge on known calendar + trend. Monthly targets are normalized by days."""
    def __init__(self,alpha=1.,transform='raw',frequency='monthly'):
        self.alpha=alpha;self.transform=transform;self.frequency=frequency
    def design(self,frame):
        dates=pd.to_datetime(frame.period)
        if self.frequency=='monthly':
            time=(dates.dt.year-2009)*12+dates.dt.month-1
            cycle=dates.dt.month.to_numpy();length=12
        else:
            time=(dates-pd.Timestamp('2009-12-07')).dt.days/7
            cycle=dates.dt.isocalendar().week.to_numpy(dtype=float);length=52
        calendar=np.column_stack([np.asarray(time,dtype=float)/length,*[np.sin(2*np.pi*k*cycle/length) for k in (1,2)],*[np.cos(2*np.pi*k*cycle/length) for k in (1,2)]])
        # Month dummies are known at forecasting time and require no future sales.
        monthly=np.column_stack([(dates.dt.month.to_numpy()==month).astype(float) for month in range(1,12)])
        return np.column_stack([calendar,monthly])
    def fit(self,frame,y):
        period_days=pd.to_datetime(frame.period).dt.days_in_month.to_numpy() if self.frequency=='monthly' else np.full(len(frame),7.)
        target=np.asarray(y,dtype=float)/period_days
        if self.transform=='log':target=np.log1p(target)
        self.scaler_=StandardScaler();matrix=self.scaler_.fit_transform(self.design(frame))
        self.reg_=Ridge(alpha=self.alpha).fit(matrix,target)
        return self
    def predict(self,frame):
        value=self.reg_.predict(self.scaler_.transform(self.design(frame)))
        if self.transform=='log':value=np.expm1(value)
        days=pd.to_datetime(frame.period).dt.days_in_month.to_numpy() if self.frequency=='monthly' else np.full(len(frame),7.)
        return np.maximum(value,0)*days


def fit_model(history,target,frequency,candidate):
    from Models.research import aggregate
    algorithm,level,transform=candidate.split('|')
    if level!='total':raise ValueError('Coursework v3 regression uses total series')
    frame=aggregate(history)
    if algorithm=='CalendarRidge1':
        return CalendarRegressor(1.,transform,frequency).fit(frame,frame[target]),['period']
    if algorithm!='RidgeShort':raise ValueError('Unknown coursework algorithm')
    enriched,columns=raw_short_features(frame,target,frequency)
    training=enriched.dropna(subset=columns+['scale',target])
    if training.empty:raise ValueError('Insufficient short-history training rows')
    return ShortRegressor(algorithm,columns,transform).fit(training,training[target]),columns


def predict(history,target,frequency,candidate,model,columns,horizon):
    from Models.research import aggregate
    algorithm,level,_=candidate.split('|')
    if level!='total':raise ValueError('Coursework v3 regression uses total series')
    if algorithm=='CalendarRidge1':
        last=history.period.max()
        periods=[last+(pd.DateOffset(months=h) if frequency=='monthly' else timedelta(weeks=h)) for h in range(1,horizon+1)]
        values=model.predict(pd.DataFrame({'period':periods}))
        result=pd.DataFrame({'period':periods,'forecast':values,'horizon':range(1,horizon+1)})
    else:
        working=aggregate(history);output=[]
        step=pd.offsets.MonthBegin(1) if frequency=='monthly' else timedelta(days=7)
        for h in range(1,horizon+1):
            period=working.period.max()+step
            future=working.groupby('product_id')[['category','product_name']].last().reset_index().assign(period=period)
            future[target]=np.nan
            enriched,_=raw_short_features(pd.concat([working,future],ignore_index=True),target,frequency)
            values=model.predict(enriched[enriched.period==period])
            if not np.isfinite(values).all():raise ValueError('Non-finite coursework prediction')
            future[target]=values;working=pd.concat([working,future],ignore_index=True)
            output.append(dict(period=period,forecast=float(values.sum()),horizon=h))
        result=pd.DataFrame(output)
    window=3 if frequency=='monthly' else 4
    shares=history.groupby('product_id').tail(window).groupby('product_id')[target].sum()
    shares=shares/shares.sum() if shares.sum()>0 else shares*0+1/len(shares)
    metadata=history.groupby('product_id')[['category','product_name']].last()
    output=pd.concat([metadata.reset_index().assign(period=r.period,horizon=r.horizon,forecast=shares.reindex(metadata.index).to_numpy()*r.forecast) for r in result.itertuples()],ignore_index=True)
    if not np.isfinite(output.forecast).all():raise ValueError('Non-finite coursework prediction')
    return output
