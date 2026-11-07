"""MySQL integration tests using an isolated SQL clone.

Only generated folmare_test_* databases are dropped. The configured application database is not reset or modified by these tests.
"""
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import csv
import io
import json
import os
import re
import time

import numpy as np
import pandas as pd
import pytest
from api.integration import create_app,SOURCE
from api.business import BusinessError
from api.store import transaction
from api.forecasting import database_daily,database_fingerprint,run_inference
from connector.connector import Connector

ROOT=Path(__file__).resolve().parents[1]

from mysql_test_support import cloned_database

@pytest.fixture(scope='session')
def mysql_clone():
    with cloned_database() as name:yield name

@pytest.fixture()
def app(mysql_clone,tmp_path):
    app=create_app(ROOT/'UI',tmp_path);app.config.update(TESTING=True,SECRET_KEY='test-only-mysql')
    return app

@pytest.fixture()
def client(app):
    client=app.test_client();token=client.get('/api/session').json['csrf']
    response=client.post('/api/login',json={'email':'manager@haircare.local','password':'DemoAdmin123!'},headers={'X-CSRF-Token':token})
    assert response.status_code==200,response.json
    client.csrf=response.json['csrf']
    return client


def post(client,path,body):
    return client.post(path,json=body,headers={'X-CSRF-Token':client.csrf})


def wait_job(engine):
    deadline=time.monotonic()+120
    while engine.job['status']=='running' and time.monotonic()<deadline:time.sleep(.05)
    assert engine.job['status']=='completed',engine.job


def test_actual_source_module_and_database_identity(client,mysql_clone):
    result=client.get('/api/ml/diagnostics')
    assert result.status_code==200,result.json
    data=result.json
    assert data['database']['name']==mysql_clone and data['database']['version'].startswith('8.')
    assert data['fallback_enabled'] is False and data['stale'] is False
    assert all(m['inside_project'] for m in data['modules'].values())
    assert all(Path(m['path']).is_relative_to(SOURCE) for m in data['modules'].values())
    with transaction() as store:
        assert data['counts']['Daily_Sales']==store.one('SELECT COUNT(*) AS n FROM Daily_Sales')['n']
        assert data['source_sha256']==database_fingerprint(database_daily(store))
        assert data['totals']['quantity']==float(store.one('SELECT SUM(quantity_sold) AS n FROM Daily_Sales')['n'])


def test_forbid_csv_seed_and_sqlite_at_runtime(client,app,monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('Business data must come from MySQL')
    monkeypatch.setattr(pd,'read_csv',forbidden)
    import sqlite3
    monkeypatch.setattr(sqlite3,'connect',forbidden)
    for path in ['/api/ml/status','/api/ml/history','/api/ml/forecast','/api/ml/recommendations','/api/admin/products','/api/admin/orders']:
        result=client.get(path);assert result.status_code==200,(path,result.json)
    engine=app.extensions['ml_engine'];engine.start_job('inference');wait_job(engine)


def test_auth_database_account_csrf_and_logout(app,client):
    other=app.test_client()
    assert other.get('/api/admin/products').status_code==401
    assert other.get('/api/ml/status').status_code==401
    assert other.post('/api/login',json={},headers={'X-CSRF-Token':'!'}).status_code==403
    token=other.get('/api/session').json['csrf']
    # Credentials not present in Admin_User must be rejected.
    assert other.post('/api/login',json={'email':'admin@haircare.vn','password':'admin123'},headers={'X-CSRF-Token':token}).status_code==401
    assert client.post('/api/ml/stock',json={}).status_code==403
    assert post(client,'/api/logout',{}).status_code==200
    assert client.get('/api/ml/status').status_code==401


def test_database_failure_has_no_cached_or_file_fallback(client,monkeypatch):
    assert client.get('/api/ml/status').status_code==200
    monkeypatch.setenv('MYSQL_PORT','1');monkeypatch.setenv('MYSQL_CONNECT_TIMEOUT','1')
    for path in ['/api/ml/status','/api/ml/forecast','/api/ml/history','/api/admin/products']:
        result=client.get(path);assert result.status_code==503
        assert 'fallback is disabled' in result.json['error']


@pytest.mark.parametrize('frequency,count',[('weekly',12),('monthly',3)])
@pytest.mark.parametrize('target',['quantity_sold','revenue_gbp'])
def test_forecast_from_database_reconciles_categories(client,frequency,count,target):
    path=f'/api/ml/forecast?frequency={frequency}&target={target}'
    result=client.get(path).json
    assert len([r for r in result['series'] if r['role']=='champion'])==count
    assert result['source']=='mysql:Forecast_Run'
    total=sum(r['forecast'] for r in result['series'] if r['role']=='champion')
    status=client.get('/api/ml/status').json
    parts=0
    for category in status['categories']:
        response=client.get(path+'&category='+category.replace(' ','%20')).json
        assert response['series']
        parts+=sum(r['forecast'] for r in response['series'] if r['role']=='champion')
    assert np.isclose(total,parts)
    assert {row['partition'] for row in result['scores']}=={'validation','test'}


@pytest.mark.parametrize('query',['frequency=invalid','target=price','category=unknown'])
def test_bad_forecast_options(client,query):assert client.get('/api/ml/forecast?'+query).status_code==400


@pytest.mark.parametrize('path',['/ml_backend/catalog.json','/runtime/inventory.sqlite3','/server.py','/.env','/views/products.html'])
def test_private_or_old_sample_files_not_served(client,path):assert client.get(path).status_code==404


@pytest.mark.parametrize('quantity',[0,-1,1.5,True,None,'1',1000001])
def test_invalid_stock_quantity(client,quantity):
    assert post(client,'/api/ml/stock',{'product_id':'8161090633945','action':'order','quantity':quantity}).status_code==400


def test_stock_writes_same_mysql_rows_and_original_audit(client):
    before=client.get('/api/ml/recommendations').json['rows'][0];key=before['product_id'];variant=before['variant_id']
    result=post(client,'/api/ml/stock',{'product_id':key,'action':'order','quantity':3})
    assert result.status_code==200,result.json
    after=result.json['row']
    assert after['incoming']==before['incoming']+3
    with transaction() as store:
        assert store.one('SELECT incoming FROM Product_Variant WHERE variant_id=%s',(variant,))['incoming']==after['incoming']
        audit=store.one('SELECT * FROM Inventory_Adjustment WHERE variant_id=%s ORDER BY adjustment_id DESC LIMIT 1',(variant,))
        assert audit['adjustment_type']=='order' and '3 units' in audit['reason']
    # Inventory and forecast must refer to the very same database record.
    product=next(p for p in client.get('/api/admin/products').json['products'] if p['variant_id']==variant)
    assert product['incoming']==after['incoming']
    result=post(client,'/api/ml/stock',{'product_id':key,'action':'receive','quantity':3})
    assert result.status_code==200,result.json
    final=result.json['row'];assert final['incoming']==before['incoming'] and final['on_hand']==before['on_hand']+3
    assert post(client,'/api/ml/stock',{'product_id':key,'action':'receive','quantity':final['incoming']+1}).status_code==400


def test_recommendations_use_database_settings_and_model_output(client):
    rows=client.get('/api/ml/recommendations').json['rows']
    with transaction() as store:settings={r['setting_key']:float(r['setting_value']) for r in store.all("SELECT * FROM Settings WHERE setting_key IN ('lead_time_days','review_period_days','safety_stock')")}
    assert rows and all(row['horizon_days']==settings['lead_time_days']+settings['review_period_days'] for row in rows)
    assert all(row['safety_stock']==settings['safety_stock'] for row in rows)
    for row in rows:
        import math
        assert row['suggested']==max(0,math.ceil(row['forecast']+row['safety_stock']-row['available']-row['incoming']))


def test_csv_export_comes_from_sql_predictions(client):
    result=client.get('/api/ml/export?frequency=monthly&target=quantity_sold&category=shampoo')
    assert result.status_code==200 and 'attachment' in result.headers['Content-Disposition']
    rows=list(csv.DictReader(io.StringIO(result.text)))
    assert rows and all(r['frequency']=='monthly' and r['category']=='shampoo' and r['target']=='quantity_sold' for r in rows)


def test_admin_lists_and_settings_match_sql(client):
    for resource,table in [('products','Product_Variant'),('orders','Order'),('customers','User'),('reviews','Review'),('vouchers','Voucher')]:
        result=client.get('/api/admin/'+resource)
        assert result.status_code==200,result.json
        with transaction() as store:count=store.one(f'SELECT COUNT(*) AS n FROM `{table}`')['n']
        assert len(result.json[resource])==count
    settings=client.get('/api/admin/settings').json['settings'];original=settings['safety_stock']
    try:
        assert post(client,'/api/admin/settings',{'safety_stock':16}).status_code==200
        assert all(r['safety_stock']==16 for r in client.get('/api/ml/recommendations').json['rows'])
    finally:assert post(client,'/api/admin/settings',{'safety_stock':float(original)}).status_code==200


def test_repeated_original_module_inference_is_finite_and_stable(app):
    engine=app.extensions['ml_engine'];before=engine.load();old=before['forecasts'].copy()
    engine.start_job('inference')
    with pytest.raises(BusinessError) as error:engine.start_job('inference')
    assert error.value.status==409
    wait_job(engine)
    after=engine.load();assert after['inference_run_id']!=before['inference_run_id']
    keys=['frequency','target','role','product_id','horizon']
    joined=old.merge(after['forecasts'],on=keys,suffixes=('_a','_b'),validate='one_to_one')
    assert len(joined)==len(old)==5310
    assert np.allclose(joined.forecast_a,joined.forecast_b)
    assert np.isfinite(joined.forecast_b).all() and (joined.forecast_b>=0).all()


def test_changed_daily_sales_rejects_stale_results_and_uses_new_sql_input(client,app):
    engine=app.extensions['ml_engine'];baseline=engine.forecast('monthly','quantity_sold','')
    old_total=sum(r['forecast'] for r in baseline['series'] if r['role']=='baseline')
    with transaction() as store:
        row=store.one('SELECT sales_id,quantity_sold FROM Daily_Sales WHERE sale_date=%s ORDER BY sales_id LIMIT 1',('2011-11-30',))
        store.execute('UPDATE Daily_Sales SET quantity_sold=quantity_sold+1000 WHERE sales_id=%s',(row['sales_id'],))
    try:
        assert client.get('/api/ml/status').json['stale'] is True
        assert client.get('/api/ml/forecast').status_code==409
        engine.start_job('inference');wait_job(engine)
        assert client.get('/api/ml/status').json['stale'] is False
        updated=engine.forecast('monthly','quantity_sold','')
        new_total=sum(r['forecast'] for r in updated['series'] if r['role']=='baseline')
        assert not np.isclose(new_total,old_total)
    finally:
        with transaction() as store:store.execute('UPDATE Daily_Sales SET quantity_sold=%s WHERE sales_id=%s',(row['quantity_sold'],row['sales_id']))
        engine.start_job('inference');wait_job(engine)


def test_failed_training_keeps_active_model_and_database_results(app,monkeypatch):
    from Models import research
    engine=app.extensions['ml_engine'];before=engine.status()
    def failed(*a,**kw):raise RuntimeError('deliberate test failure')
    monkeypatch.setattr(research,'train',failed)
    engine.start_job('train');deadline=time.monotonic()+10
    while engine.job['status']=='running' and time.monotonic()<deadline:time.sleep(.05)
    assert engine.job['status']=='failed'
    after=engine.status();assert after['metadata']['run_id']==before['metadata']['run_id']
    assert after['inference_run_id']==before['inference_run_id'] and after['stale'] is False


def test_concurrent_stock_orders_preserve_all_mysql_updates(app,client):
    engine=app.extensions['ml_engine'];row=engine.recommendations()[0]
    user={'role':'admin','user_id':1}
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _:engine.stock_action(row['product_id'],'order',1,user),range(8)))
    with transaction() as store:
        after=store.one('SELECT incoming FROM Product_Variant WHERE variant_id=%s',(row['variant_id'],))
    assert after['incoming']==row['incoming']+8
