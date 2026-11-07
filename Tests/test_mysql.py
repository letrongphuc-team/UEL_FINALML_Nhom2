"""Integration tests use their own randomly named database; never reset haircare_shop."""
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
import os
import re
import pytest
from connector.connector import Connector
from connector.migrate import migrate
from api.store import transaction
from api.app import app

@pytest.fixture()
def database(monkeypatch):
    name='haircare_test_'+uuid4().hex[:12]
    assert re.fullmatch(r'haircare_test_[0-9a-f]{12}',name)
    db=Connector();db.connect()
    try:
        db.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4')
    except Exception:
        db.close();raise
    monkeypatch.setenv('MYSQL_DATABASE',name)
    test=Connector();test.connect()
    try:
        sql=(Path(__file__).resolve().parents[1]/'connector/schema.sql').read_text(encoding='utf-8').replace('USE haircare_shop;',f'USE `{name}`;')
        for statement in sql.split(';'):
            if statement.strip():test.execute(statement)
        migrate()
        with transaction() as s:
            s.execute("INSERT INTO Product (product_id,name,category) VALUES ('test-product','Test Shampoo','shampoo')")
            s.execute("INSERT INTO Product_Variant (product_id,sku_code,size,price,on_hand) VALUES ('test-product','TEST-1','Full',30,2)")
            admin=s.register(dict(email='admin@test.local',password='password123',name='Admin'))
            from werkzeug.security import generate_password_hash
            s.execute('INSERT INTO Admin_User (email,password_hash,full_name,role) VALUES (%s,%s,%s,%s)',('manager@test.local',generate_password_hash('password123'),'Manager','admin'))
        app.config.update(TESTING=True)
        yield name
    finally:
        test.close()
        # The only destructive command targets the verified, generated test database.
        assert name.startswith('haircare_test_')
        db.execute(f'DROP DATABASE `{name}`');db.close()

def client(email='buyer@test.local',admin=False):
    c=app.test_client();csrf=c.get('/api/session').json['csrf']
    action='login' if admin else 'register'
    r=c.post('/api/auth/'+action,json=dict(email=email,password='password123',name='Buyer',role='admin' if admin else 'customer'),headers={'X-CSRF-Token':csrf})
    assert r.status_code==200,r.json
    return c,{'X-CSRF-Token':r.json['csrf']}

def payload(key='key1',method='COD',quantity=1):
    return dict(checkout_key=key,customer_name='Buyer',phone='0901234567',address='Test address',payment_method=method,items=[dict(variant_id=1,quantity=quantity,price=.01)],discount=999)

def test_checkout_ownership_idempotency_and_price(database):
    c,h=client();r=c.post('/api/customer/orders',json=payload(),headers=h)
    assert r.status_code==201,r.json
    assert r.json['order_total']==35 and r.json['subtotal']==30
    order_id=r.json['order_id']
    assert c.post('/api/customer/orders',json=payload(),headers=h).json['order_id']==order_id
    other,oh=client('other@test.local')
    assert other.get(f'/api/customer/orders/{order_id}').status_code==404
    assert c.get('/api/admin/orders').status_code==403
    assert c.get(f'/api/admin/orders/{order_id}').status_code==403
    with transaction() as s:
        assert s.one('SELECT reserved FROM Product_Variant WHERE variant_id=1')['reserved']==1
        assert s.one('SELECT COUNT(*) AS n FROM `Order`')['n']==1

def test_failed_checkout_rolls_back(database):
    c,h=client()
    r=c.post('/api/customer/orders',json=payload(quantity=3),headers=h)
    assert r.status_code==409
    with transaction() as s:
        assert s.one('SELECT COUNT(*) AS n FROM `Order`')['n']==0
        assert s.one('SELECT reserved FROM Product_Variant WHERE variant_id=1')['reserved']==0
    assert app.test_client().post('/api/customer/orders',json=payload()).status_code==403
    assert app.test_client().get('/api/customer/orders').status_code==401

def test_bank_payment_shipping_completion_return(database):
    c,h=client();o=c.post('/api/customer/orders',json=payload(method='Bank Transfer'),headers=h).json;oid=o['order_id']
    data=dict(reference=o['payment_reference'],amount=o['order_total'],outcome='paid')
    assert c.post(f'/api/customer/orders/{oid}/payment',json={**data,'amount':1},headers=h).status_code==400
    for _ in range(2):assert c.post(f'/api/customer/orders/{oid}/payment',json=data,headers=h).json['status']=='processing'
    a,ah=client('manager@test.local',True)
    for status in ['shipping','completed']:
        result=a.post(f'/api/admin/orders/{oid}/transition',json=dict(status=status),headers=ah)
        assert result.status_code==200,result.json
    item=result.json['items'][0]['order_item_id']
    assert c.post('/api/customer/reviews',json=dict(order_item_id=item,rating=5,content='Good'),headers=h).status_code==200
    assert c.post('/api/customer/reviews',json=dict(order_item_id=item,rating=5),headers=h).status_code==409
    assert c.post(f'/api/customer/orders/{oid}/transition',json=dict(status='return_pending',reason='Unopened'),headers=h).status_code==200
    assert a.post(f'/api/admin/orders/{oid}/transition',json=dict(status='returned'),headers=ah).json['payments'][0]['status']=='refunded'
    with transaction() as s:
        stock=s.one('SELECT * FROM Product_Variant WHERE variant_id=1')
        assert stock['on_hand']==2 and stock['reserved']==0

def test_expiry_and_failed_payment_release_stock(database):
    c,h=client();o=c.post('/api/customer/orders',json=payload(method='Bank Transfer'),headers=h).json
    with transaction() as s:s.execute('UPDATE `Order` SET expires_at=%s WHERE order_id=%s',(datetime.now()-timedelta(minutes=1),o['order_id']))
    assert c.get('/api/customer/orders/'+str(o['order_id'])).json['status']=='cancelled'
    o=c.post('/api/customer/orders',json=payload('key2','Bank Transfer'),headers=h).json
    result=c.post(f'/api/customer/orders/{o["order_id"]}/payment',json=dict(reference=o['payment_reference'],amount=o['order_total'],outcome='failed'),headers=h)
    assert result.json['status']=='cancelled'
    with transaction() as s:assert s.one('SELECT reserved FROM Product_Variant WHERE variant_id=1')['reserved']==0

def test_concurrent_last_stock(database):
    c,h=client();d,dh=client('second@test.local')
    with transaction() as s:
        s.execute('UPDATE Product_Variant SET on_hand=1 WHERE variant_id=1')
    def buy(args):
        browser,headers,key=args
        return browser.post('/api/customer/orders',json=payload(key),headers=headers).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(buy,[(c,h,'one'),(d,dh,'two')]))
    assert sorted(statuses)==[201,409]
    with transaction() as s:assert s.one('SELECT reserved FROM Product_Variant WHERE variant_id=1')['reserved']==1

def test_admin_management_profile_cart_and_voucher(database):
    c,h=client();a,ah=client('manager@test.local',True)
    saved=a.post('/api/admin/products',json=dict(name='New Product',category='treatment',price='1.99',size='Full'),headers=ah)
    assert saved.status_code==200,saved.json
    variant=next(p for p in a.get('/api/admin/products').json['products'] if p['product_id']==saved.json['product_id'])
    vid=variant['variant_id']
    for action,quantity in [('adjust',10),('order',5),('receive',5)]:
        r=a.post('/api/admin/inventory',json=dict(variant_id=vid,action=action,quantity=quantity,reason='QA seed'),headers=ah)
        assert r.status_code==200,r.json
    assert a.post('/api/admin/settings',json=dict(shipping_fee='2.50'),headers=ah).status_code==200
    assert a.post('/api/admin/vouchers',json=dict(code='SAVE10',discount_type='percentage',discount_value=10,usage_limit=1,status='active'),headers=ah).status_code==200
    assert c.put('/api/customer/profile',json=dict(name='Buyer',phone='0901234567',addresses=['First address','Second address'],hair_type='curly'),headers=h).json['profile']['addresses']==['First address','Second address']
    assert c.put('/api/customer/cart',json=dict(items=[dict(variant_id=vid,quantity=2)]),headers=h).status_code==200
    assert c.get('/api/customer/cart').json['items']==[dict(variant_id=vid,quantity=2)]
    items=[dict(variant_id=vid,quantity=2)]
    q=c.post('/api/customer/quote',json=dict(items=items,voucher='SAVE10'),headers=h)
    assert q.json['total']==6.08,q.json
    data={**payload(), 'items':items,'voucher':'SAVE10'}
    assert c.post('/api/customer/orders',json=data,headers=h).status_code==201
    assert c.post('/api/customer/orders',json={**data,'checkout_key':'another'},headers=h).status_code==400
    assert c.get('/api/customer/cart').json['items']==[]
    users=a.get('/api/admin/customers').json['customers'];buyer=next(u for u in users if u['email']=='buyer@test.local')
    assert len(a.get('/api/admin/customers/'+str(buyer['user_id'])).json['orders'])==1
    assert a.post('/api/admin/customers',json=dict(user_id=buyer['user_id'],status='locked'),headers=ah).status_code==200
    assert c.get('/api/customer/orders').status_code==401
