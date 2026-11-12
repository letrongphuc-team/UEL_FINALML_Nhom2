"""FOLMARÉ administrative API for forecasting and inventory management.

Business records and forecast responses come from MySQL. Missing/failed database
connections never fall back to CSV, JSON seed files, SQLite or localStorage.
"""
from pathlib import Path
from functools import wraps
from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
import os
import secrets
import threading
import uuid

import numpy as np
import pandas as pd
from flask import Flask, jsonify, request, session, send_from_directory, redirect, Response
from mysql.connector import Error as MySQLError
from api.business import BusinessError
from api.store import transaction, Store
from api.forecasting import active_folder, database_daily, database_fingerprint, latest_forecast, run_inference
from api.analytics import historical_data
from connector.connector import Connector
from api.app import app as source_app, JSONProvider

SOURCE=Path(inspect.getfile(Connector)).resolve().parents[1]
CATEGORIES={'shampoo':'Shampoo','conditioner':'Conditioner','hairmask':'Hair Masks',
            'treatment':'Treatment / Oil','hairstyling':'Styling & Finishing','dry shampoo':'Dry Shampoo'}


def records(frame):
    return json.loads(frame.to_json(orient='records',date_format='iso'))


def decode(value):
    return json.loads(value) if isinstance(value,str) else value


class Engine:
    def __init__(self,runtime):
        self.runtime=Path(runtime)
        self.runtime.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.job_lock=threading.RLock()
        self.job=None
        self.history_cache=None

    def load(self):
        # Always reach MySQL, even when derived history is cached in memory.
        with self.lock,transaction() as store:
            identity=store.one('SELECT DATABASE() AS name,VERSION() AS version')
            daily=database_daily(store)
            fingerprint=database_fingerprint(daily)
            catalog=store.products(True)
            settings={r['setting_key']:r['setting_value'] for r in store.all('SELECT * FROM Settings')}
            latest = latest_forecast(store)
            model = json.loads((active_folder(latest) / 'metadata.json').read_text())
            if latest:
                metadata=decode(latest['metadata_json'])
                frame=pd.DataFrame(decode(latest['predictions_json']))
                if not frame.empty:
                    frame['product_id']=frame.product_id.astype(str)
                    frame['period']=pd.to_datetime(frame.period).dt.strftime('%Y-%m-%d')
                stale=(metadata.get('source_sha256')!=fingerprint or metadata.get('database')!=identity['name']
                       or metadata.get('run_id')!=model['run_id'])
            else:
                metadata=model;frame=pd.DataFrame();stale=True
            if not self.history_cache or self.history_cache[0]!=fingerprint:
                self.history_cache=(fingerprint,historical_data(daily,'MySQL '+identity['name']+'.Daily_Sales'))
            flags=store.one('SELECT COUNT(*) AS rows_total,SUM(is_simulated=1) AS mapped_rows FROM Daily_Sales')
        return dict(daily=daily,history=self.history_cache[1],catalog=catalog,metadata=metadata,
                    forecasts=frame,source_sha256=fingerprint,database=identity,settings=settings,
                    inference_run_id=latest['run_id'] if latest else None,
                    generated_at=latest['created_at'] if latest else None,stale=stale,source_flags=flags)

    def status(self):
        data=self.load();history=data['history'];model_products=data['daily'].product_id.nunique()
        db=Connector()
        return dict(metadata=data['metadata'],generated_at=data['generated_at'],job=self.job,
                    products=int(model_products),catalog_products=len(data['catalog']),rows=len(data['daily']),
                    categories={k:CATEGORIES.get(k,k) for k in sorted(data['daily'].category.unique())},
                    forecast_rows=len(data['forecasts']),source_start=history['source_start'],source_end=history['source_end'],
                    source='mysql:Daily_Sales',source_sha256=data['source_sha256'],source_flags=data['source_flags'],
                    database={**data['database'],'host':db.server,'port':db.port},stale=data['stale'],
                    inference_run_id=data['inference_run_id'],currency='GBP',is_simulated=bool(data['source_flags']['mapped_rows']))

    def require_forecasts(self,data):
        if data['stale'] or data['forecasts'].empty:
            raise BusinessError('Database/model input has changed. Run inference to publish current MySQL predictions.',409)

    def forecast(self,frequency,target,category):
        data=self.load();self.require_forecasts(data)
        frame=data['forecasts'];selected=frame[(frame.frequency==frequency)&(frame.target==target)]
        history=pd.DataFrame(data['history'][frequency])
        if category:
            selected=selected[selected.category==category];history=history[history.category==category]
        grouped=selected.groupby(['period','role','model','horizon'],as_index=False).forecast.sum()
        actual=history.groupby('period',as_index=False)[target].sum().rename(columns={target:'actual'})
        actual['period']=pd.to_datetime(actual.period).dt.strftime('%Y-%m-%d')
        choices=data['metadata']['selections'][frequency+':'+target]
        scores=[r for r in data['metadata'].get('scores',[]) if r['frequency']==frequency and r['target']==target and r['model'] in choices.values()]
        return dict(series=records(grouped),history=records(actual.tail(12 if frequency=='monthly' else 16)),scores=scores,
                    selections=choices,run_id=data['metadata']['run_id'],inference_run_id=data['inference_run_id'],
                    currency='GBP' if target=='revenue_gbp' else 'units',category=category,source='mysql:Forecast_Run',
                    improvement=data['metadata'].get('improvement_comparison',{}).get(frequency+':'+target),
                    source_sha256=data['source_sha256'],note='Metrics stored with the validated model in MySQL cover the full source panel. WAPE is error, not accuracy.')

    def history(self):
        data=self.load();h=data['history']
        return dict(**{k:h[k] for k in ('source_start','source_end','complete_month_end','complete_week_end','note')},
                    monthly=records(pd.DataFrame(h['monthly']).groupby('period',as_index=False)[['quantity_sold','revenue_gbp']].sum()),
                    categories=records(data['daily'].groupby('category',as_index=False)[['quantity_sold','revenue_gbp']].sum()),
                    products=int(data['daily'].product_id.nunique()),rows=len(data['daily']),source='mysql:Daily_Sales',
                    source_sha256=data['source_sha256'])

    def recommendations(self):
        data=self.load();self.require_forecasts(data)
        frame=data['forecasts']
        monthly=frame[(frame.frequency=='monthly')&(frame.target=='quantity_sold')&(frame.role=='champion')]
        first=monthly[monthly.horizon==1].set_index('product_id')
        settings=data['settings']
        lead=int(float(settings['lead_time_days']));review=int(float(settings['review_period_days']));safety=float(settings['safety_stock'])
        rows=[];seen=set()
        for product in data['catalog']:
            key=str(product['product_id'])
            if key not in first.index:continue
            if key in seen:raise BusinessError('Multiple variants require a validated SKU allocation; no allocation is invented.',409)
            seen.add(key)
            forecast=first.loc[key]
            period=pd.Timestamp(forecast.period)
            demand=float(forecast.forecast)*(lead+review)/period.days_in_month
            available=product['on_hand']-product['reserved']
            rows.append(dict(product_id=key,variant_id=product['variant_id'],sku=product['sku_code'],name=product['name'],
                             size=product['size'],category=product['category'],forecast=demand,safety_stock=safety,
                             **{k:product[k] for k in ('on_hand','reserved','incoming')},available=available,
                             suggested=max(0,math.ceil(demand+safety-available-product['incoming'])),
                             lead_time_days=lead,review_period_days=review,horizon_days=lead+review))
        if len(seen)!=len(first):raise BusinessError('A forecast product is missing from Product_Variant',409)
        return sorted(rows,key=lambda row:(-row['suggested'],row['sku']))

    def stock_action(self,key,action,quantity,user,reason='FOLMARE database inventory action'):
        with transaction() as store:
            rows=store.all('SELECT variant_id FROM Product_Variant WHERE product_id=%s ORDER BY variant_id',(key,))
            if not rows:raise BusinessError('Unknown database product',404)
            if len(rows)!=1:raise BusinessError('Select a specific variant for this product',409)
            store.inventory(user,dict(variant_id=rows[0]['variant_id'],action=action,quantity=quantity,reason=f'{reason}: {action} {quantity} units'))
        # Recommendations use the same live Product_Variant rows after the committed write.
        return next((r for r in self.recommendations() if r['product_id']==key),None)

    def events(self):
        with transaction() as store:
            return store.all('''SELECT ia.adjustment_id AS id,p.product_id,p.name,ia.adjustment_type AS action,
                ia.quantity_delta AS quantity,ia.reason,ia.created_at FROM Inventory_Adjustment ia
                JOIN Product_Variant v ON v.variant_id=ia.variant_id JOIN Product p ON p.product_id=v.product_id
                ORDER BY ia.created_at DESC,ia.adjustment_id DESC LIMIT 100''')

    def start_job(self,action):
        with self.job_lock:
            if self.job and self.job['status']=='running':raise BusinessError('A model job is already running',409)
            # Verify DB access before claiming the job was accepted.
            with transaction() as store:database_daily(store)
            self.job=dict(id=uuid.uuid4().hex,action=action,status='running',started_at=datetime.now(timezone.utc).isoformat(),message='Training Ridge and CatBoost from MySQL…' if action=='train' else 'Updating forecasts from MySQL…')
            threading.Thread(target=self.work,args=(action,),daemon=True).start()
            return dict(self.job)

    def work(self, action):
        try:
            status = 'completed'
            evaluation = []
            if action == 'inference':
                with self.lock, transaction() as store:
                    result = run_inference(store)
                message = f'Inference complete: {len(result["forecasts"]):,} MySQL predictions; fitted model, no training.'
            else:
                from Models.retrain import train

                with transaction() as store:
                    daily = database_daily(store)
                    incumbent = active_folder(latest_forecast(store))
                fingerprint = database_fingerprint(daily)
                folder, evaluation, message = train(daily, incumbent)
                if folder is None:
                    status = 'rejected'
                else:
                    # The committed Forecast_Run row publishes both the forecast
                    # and the model reference. A failed write leaves both unchanged.
                    with self.lock, transaction() as store:
                        run_inference(store, folder=folder, expected_incumbent=incumbent,
                                      expected_fingerprint=fingerprint)
            with self.job_lock:
                self.job.update(status=status, message=message, evaluation=evaluation,
                                completed_at=datetime.now(timezone.utc).isoformat())
        except Exception as error:
            import traceback
            message = str(error) if isinstance(error, BusinessError) else 'Could not finish the model job. Please try again.'
            with self.job_lock:
                self.job.update(status='failed', message=message,
                                completed_at=datetime.now(timezone.utc).isoformat())
            (self.runtime / 'last-job-error.log').write_text(traceback.format_exc())

    def diagnostics(self):
        data=self.load()
        import api.forecasting,api.store,Models.research as research
        modules={}
        from Models import coursework_models,coursework_training
        for module in (api.forecasting,api.store,research,coursework_models,coursework_training):
            path=Path(inspect.getfile(module)).resolve()
            modules[module.__name__]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                                      'inside_project':path.is_relative_to(SOURCE)}
        with transaction() as store:
            counts={name:store.one(f'SELECT COUNT(*) AS n FROM `{name}`')['n'] for name in
                    ('Product','Product_Variant','Daily_Sales','Forecast_Run','Inventory_Forecast','Inventory_Recommendation','Order','Review')}
            totals=store.one('SELECT SUM(quantity_sold) AS quantity,SUM(revenue) AS revenue_gbp,MIN(sale_date) AS start,MAX(sale_date) AS end FROM Daily_Sales WHERE is_simulated=1')
            mappings=store.one('SELECT COUNT(*) AS rows_total,SUM(is_simulated=1) AS mapped_rows FROM Product_Source_Mapping')
        return dict(database=data['database'],counts=counts,totals=totals,mapping_flags=mappings,modules=modules,
                    source_sha256=data['source_sha256'],source_flags=data['source_flags'],stale=data['stale'],
                    active_model_run=data['metadata']['run_id'],inference_run_id=data['inference_run_id'],
                    source='MySQL → forecasting modules → Forecast_Run → FOLMARE',fallback_enabled=False)


def create_app(web_root,runtime=None):
    web_root=Path(web_root)
    app=Flask(__name__,static_folder=None)
    app.json=JSONProvider(app)
    app.config.update(SECRET_KEY=os.environ.get('FOLMARE_SECRET') or secrets.token_hex(32),SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Strict',MAX_CONTENT_LENGTH=64*1024)
    engine=Engine(runtime or web_root/'runtime');app.extensions['ml_engine']=engine

    def protected(fn):
        @wraps(fn)
        def wrapped(*args,**kwargs):
            user=session.get('user')
            if not user or user.get('role')!='admin':raise BusinessError('Database admin sign in required',401)
            with transaction() as store:store.validate_user(user)
            return fn(*args,**kwargs)
        return wrapped

    @app.before_request
    def csrf():
        if request.path.startswith('/api/') and request.method in ('POST','PUT','PATCH','DELETE'):
            token=session.get('csrf')
            if not token or not secrets.compare_digest(request.headers.get('X-CSRF-Token',''),token):raise BusinessError('Refresh the page before submitting',403)

    @app.errorhandler(BusinessError)
    def invalid(error):return jsonify(error=str(error)),error.status

    @app.errorhandler(MySQLError)
    def unavailable(error):
        app.logger.error('MySQL error %s',getattr(error,'errno',None))
        return jsonify(error='MySQL is unavailable. Start the configured database; CSV/seed/SQLite fallback is disabled.'),503

    @app.errorhandler(413)
    def oversized(error):return jsonify(error='Request is too large'),413

    @app.errorhandler(404)
    def missing(error):return jsonify(error='Not found'),404

    @app.errorhandler(ValueError)
    @app.errorhandler(KeyError)
    @app.errorhandler(TypeError)
    def input_error(error):return jsonify(error='Invalid or missing request fields'),400

    @app.get('/api/session')
    def current_session():
        session.setdefault('csrf',secrets.token_hex(32))
        user=session.get('user')
        return jsonify(signed_in=bool(user),user=user,csrf=session['csrf'],mode='mysql')

    @app.post('/api/login')
    def login():
        body=request.get_json(silent=True)
        if not isinstance(body,dict):raise BusinessError('Invalid login request')
        with transaction() as store:user=store.login({**body,'role':'admin'})
        session.clear();session.update(user=user,csrf=secrets.token_hex(32))
        return jsonify(signed_in=True,user=user,csrf=session['csrf'])

    @app.post('/api/logout')
    def logout():
        session.clear();return jsonify(signed_in=False)

    # Register authenticated administrative business handlers.
    for rule in source_app.url_map.iter_rules():
        if rule.rule.startswith('/api/admin/') and rule.rule not in ('/api/admin/ml','/api/admin/history'):
            app.add_url_rule(rule.rule,endpoint='webml_'+rule.endpoint,view_func=source_app.view_functions[rule.endpoint],
                             methods=sorted(rule.methods-{'HEAD','OPTIONS'}))

    @app.get('/api/ml/status')
    @protected
    def status():return jsonify(engine.status())

    def options():
        frequency=request.args.get('frequency','weekly');target=request.args.get('target','revenue_gbp');category=request.args.get('category','')
        if frequency not in ('weekly','monthly') or target not in ('revenue_gbp','quantity_sold') or (category and category not in CATEGORIES):raise BusinessError('Invalid forecast options')
        return frequency,target,category

    @app.get('/api/ml/forecast')
    @protected
    def forecast():return jsonify(engine.forecast(*options()))

    @app.get('/api/ml/history')
    @app.get('/api/admin/history')
    @protected
    def history():return jsonify(engine.history())

    @app.get('/api/ml/recommendations')
    @protected
    def recommendations():
        rows=engine.recommendations()
        return jsonify(rows=rows,currency='units',horizon_days=rows[0]['horizon_days'] if rows else None,source='mysql:Product_Variant + Forecast_Run + Settings')

    @app.get('/api/ml/stock-events')
    @protected
    def events():return jsonify(events=engine.events())

    @app.post('/api/ml/stock')
    @protected
    def stock():
        body=request.get_json(silent=True)
        if not isinstance(body,dict):raise BusinessError('Invalid stock request')
        quantity=body.get('quantity');key=body.get('product_id')
        if isinstance(quantity,bool) or not isinstance(quantity,int) or not 1<=quantity<=1000000:raise BusinessError('Quantity must be a whole number from 1 to 1,000,000')
        if not isinstance(key,str):raise BusinessError('Product ID is required')
        return jsonify(row=engine.stock_action(key,body.get('action'),quantity,session['user']))

    @app.post('/api/ml/jobs')
    @protected
    def jobs():
        body=request.get_json(silent=True)
        if not isinstance(body,dict) or body.get('action') not in ('inference','train'):raise BusinessError('Choose inference or train')
        return jsonify(job=engine.start_job(body['action'])),202

    @app.get('/api/ml/diagnostics')
    @protected
    def diagnostics():return jsonify(engine.diagnostics())

    @app.get('/api/ml/export')
    @protected
    def export():
        frequency,target,category=options();data=engine.load();engine.require_forecasts(data);frame=data['forecasts']
        frame=frame[(frame.frequency==frequency)&(frame.target==target)]
        if category:frame=frame[frame.category==category]
        return Response(frame.to_csv(index=False),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=folmare-forecast.csv'})

    @app.get('/')
    def index():return send_from_directory(web_root,'index.html')

    @app.get('/admin')
    @app.get('/admin/<path:route>')
    def admin(route='dashboard'):return redirect('/#/admin/'+route)

    @app.get('/product-images/<path:filename>')
    def product_images(filename):return send_from_directory(SOURCE/'images',filename)

    @app.get('/<path:filename>')
    def asset(filename):
        if filename.split('/')[0] not in ('assets','css','js','docs'):raise BusinessError('Not found',404)
        return send_from_directory(web_root,filename)
    return app
