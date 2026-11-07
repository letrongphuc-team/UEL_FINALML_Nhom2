"""HairCare business API handlers, mounted by main.py. No training on startup."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import json
import os
import secrets
from datetime import datetime,timedelta
from decimal import Decimal
from functools import wraps
from flask import Flask,request,session,jsonify,send_from_directory,redirect
from flask.json.provider import DefaultJSONProvider
from werkzeug.security import generate_password_hash,check_password_hash
from mysql.connector import Error as MySQLError
from api.store import transaction
from api.business import BusinessError,money,positive_int

class JSONProvider(DefaultJSONProvider):
    @staticmethod
    def default(value):
        if isinstance(value,Decimal): return float(value)
        if hasattr(value,'isoformat'): return value.isoformat()
        return DefaultJSONProvider.default(value)

app=Flask(__name__)
app.json=JSONProvider(app)
app.config.update(SECRET_KEY=os.getenv('FLASK_SECRET_KEY') or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Strict',MAX_CONTENT_LENGTH=2*1024*1024)

def endpoint(role=None):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args,**kwargs):
            user=session.get('user')
            required='admin' if request.path.startswith('/api/admin/') else role
            if required and (not user or (required=='admin' and user['role']!='admin')):
                raise BusinessError('Sign in required' if not user else 'Admin permission required',401 if not user else 403)
            with transaction() as store:
                if user: store.validate_user(user)
                store.expire()
            with transaction() as store: return fn(store,user,*args,**kwargs)
        return wrapped
    return decorate

@app.before_request
def csrf_guard():
    if request.path.startswith('/api/') and request.method in ('POST','PUT','PATCH','DELETE'):
        if not secrets.compare_digest(request.headers.get('X-CSRF-Token',''),session.get('csrf','!')):
            raise BusinessError('Refresh the page before submitting this request',403)

@app.errorhandler(BusinessError)
def business_error(error): return jsonify(error=str(error)),error.status

@app.errorhandler(MySQLError)
def database_error(error):
    app.logger.error('MySQL operation failed: %s (errno=%s)',type(error).__name__,getattr(error,'errno',None))
    return jsonify(error='Database unavailable or schema not migrated. Check MySQL and run database/migrate.py.'),503

@app.errorhandler(ValueError)
@app.errorhandler(KeyError)
@app.errorhandler(TypeError)
def input_error(error): return jsonify(error='Invalid or missing request fields'),400

@app.get('/api/session')
def current_session():
    session.setdefault('csrf',secrets.token_hex(32))
    return jsonify(user=session.get('user'),csrf=session['csrf'],mode='local',payment_simulation=os.getenv('PAYMENT_SIMULATION','true').lower()=='true')

@app.post('/api/auth/<action>')
@endpoint()
def auth(store,user,action):
    data=request.get_json() or {}
    if action in ('register','login'):
        result=store.register(data) if action=='register' else store.login(data)
        session.clear()
        session.update(user=result,csrf=secrets.token_hex(32))
        return jsonify(user=result,csrf=session['csrf'])
    if action=='logout':
        session.clear()
        session['csrf']=secrets.token_hex(32)
        return jsonify(user=None,csrf=session['csrf'])
    if action=='reset-request':
        email=str(data.get('email','')).strip().lower()
        code=secrets.token_hex(4).upper()
        if store.one('SELECT user_id FROM `User` WHERE email=%s',(email,)):
            store.execute('''INSERT INTO Password_Reset (email,code_hash,expires_at) VALUES (%s,%s,%s)
                ON DUPLICATE KEY UPDATE code_hash=VALUES(code_hash),expires_at=VALUES(expires_at)''',
                (email,generate_password_hash(code),datetime.now()+timedelta(minutes=10)))
        return jsonify(message='Demo reset code valid for 10 minutes',demo_code=code)
    if action=='reset-confirm':
        row=store.one('SELECT * FROM Password_Reset WHERE email=%s FOR UPDATE',(data.get('email'),))
        if not row or row['expires_at']<datetime.now() or not check_password_hash(row['code_hash'],str(data.get('code',''))):
            raise BusinessError('Invalid or expired reset code')
        if len(str(data.get('password','')))<8: raise BusinessError('Password requires at least 8 characters')
        store.execute('UPDATE `User` SET password_hash=%s WHERE email=%s',(generate_password_hash(data['password']),data['email']))
        store.execute('DELETE FROM Password_Reset WHERE email=%s',(data['email'],))
        return jsonify(message='Password reset complete')
    raise BusinessError('Unknown authentication action',404)

@app.get('/api/customer/products')
@endpoint()
def products(store,user): return jsonify(products=store.products(),currency='USD')

@app.route('/api/customer/cart',methods=['GET','PUT'])
@endpoint('customer')
def cart(store,user):
    if user['role']!='customer': raise BusinessError('Customer account required',403)
    return jsonify(items=store.cart(user['user_id'],request.get_json() if request.method=='PUT' else None))

@app.post('/api/customer/quote')
@endpoint('customer')
def quote_order(store,user):
    _,_,totals=store.get_quote(request.get_json() or {})
    return jsonify(totals)

@app.route('/api/customer/orders',methods=['GET','POST'])
@endpoint('customer')
def orders(store,user):
    if user['role']!='customer': raise BusinessError('Customer account required',403)
    if request.method=='POST': return jsonify(store.checkout(user,request.get_json() or {})),201
    return jsonify(orders=store.orders(user))

@app.get('/api/customer/orders/<int:order_id>')
@app.get('/api/admin/orders/<int:order_id>')
@endpoint('customer')
def order_detail(store,user,order_id): return jsonify(store.order(order_id,user))

@app.post('/api/customer/orders/<int:order_id>/transition')
@app.post('/api/admin/orders/<int:order_id>/transition')
@endpoint('customer')
def transition(store,user,order_id):
    data=request.get_json() or {}
    return jsonify(store.transition(order_id,user,data.get('status'),data.get('reason','')))

@app.post('/api/customer/orders/<int:order_id>/payment')
@endpoint('customer')
def payment(store,user,order_id):
    if os.getenv('PAYMENT_SIMULATION','true').lower()!='true': raise BusinessError('Payment simulation disabled',403)
    return jsonify(store.pay(order_id,user,request.get_json() or {}))

@app.route('/api/customer/profile',methods=['GET','PUT'])
@endpoint('customer')
def profile(store,user):
    if user['role']!='customer': raise BusinessError('Customer account required',403)
    if request.method=='PUT':
        data=request.get_json() or {}
        store.execute('UPDATE `User` SET full_name=%s,phone=%s WHERE user_id=%s',(data.get('name'),data.get('phone'),user['user_id']))
        if 'addresses' in data:
            store.execute('UPDATE `User` SET addresses_json=%s WHERE user_id=%s',(json.dumps(data['addresses']),user['user_id']))
        if data.get('password'):
            row=store.one('SELECT password_hash FROM `User` WHERE user_id=%s',(user['user_id'],))
            if len(data['password'])<8 or not check_password_hash(row['password_hash'],str(data.get('current_password',''))):
                raise BusinessError('Current password required; new password must have 8 characters')
            store.execute('UPDATE `User` SET password_hash=%s WHERE user_id=%s',(generate_password_hash(data['password']),user['user_id']))
        store.execute('DELETE FROM Hair_Profile WHERE user_id=%s',(user['user_id'],))
        store.execute('INSERT INTO Hair_Profile (user_id,hair_type,hair_condition,scalp_condition,goals_json) VALUES (%s,%s,%s,%s,%s)',
            (user['user_id'],data.get('hair_type',''),data.get('hair_condition',''),data.get('scalp_condition',''),json.dumps(data.get('goals',[]))))
    result=store.one('SELECT email,full_name,phone,addresses_json FROM `User` WHERE user_id=%s',(user['user_id'],))
    result['addresses']=json.loads(result.pop('addresses_json') or '[]')
    return jsonify(profile=result,hair=store.one('SELECT * FROM Hair_Profile WHERE user_id=%s ORDER BY hair_profile_id DESC LIMIT 1',(user['user_id'],)))

@app.route('/api/customer/reviews',methods=['GET','POST'])
@endpoint()
def reviews(store,user):
    if request.method=='POST':
        if not user or user['role']!='customer': raise BusinessError('Customer sign in required',401)
        data=request.get_json() or {}
        item=store.one('SELECT oi.*,o.status,o.user_id FROM Order_Item oi JOIN `Order` o ON o.order_id=oi.order_id WHERE oi.order_item_id=%s FOR UPDATE',(data.get('order_item_id'),))
        if not item or item['user_id']!=user['user_id'] or item['status']!='completed': raise BusinessError('Only completed purchases can be reviewed',403)
        if store.one('SELECT review_id FROM Review WHERE order_item_id=%s',(item['order_item_id'],)): raise BusinessError('Purchase already reviewed',409)
        rating=positive_int(data.get('rating'))
        if rating>5: raise BusinessError('Rating must be from 1 to 5')
        variant=store.one('SELECT product_id FROM Product_Variant WHERE variant_id=%s',(item['variant_id'],))
        store.execute('INSERT INTO Review (product_id,user_id,order_item_id,rating,title,content) VALUES (%s,%s,%s,%s,%s,%s)',
            (variant['product_id'],user['user_id'],item['order_item_id'],rating,data.get('title',''),data.get('content','')))
    return jsonify(reviews=store.all("SELECT product_id,rating,title,content FROM Review WHERE status='published'"))

@app.get('/api/admin/orders')
@endpoint('admin')
def admin_orders(store,user): return jsonify(orders=store.orders(user))

@app.get('/api/admin/products')
@endpoint('admin')
def admin_products(store,user): return jsonify(products=store.products(True))

@app.get('/api/admin/customers/<int:user_id>')
@endpoint('admin')
def customer_detail(store,user,user_id):
    profile=store.one('SELECT user_id,email,full_name,phone,status FROM `User` WHERE user_id=%s',(user_id,))
    if not profile: raise BusinessError('Customer not found',404)
    return jsonify(profile=profile,hair=store.one('SELECT * FROM Hair_Profile WHERE user_id=%s ORDER BY hair_profile_id DESC LIMIT 1',(user_id,)),
        orders=[o for o in store.orders(user) if o['user_id']==user_id])

@app.post('/api/admin/products')
@endpoint('admin')
def save_product(store,user):
    data=request.get_json() or {}
    key=str(data.get('product_id') or secrets.token_hex(8))
    if not data.get('name') or money(data.get('price',0))<=0: raise BusinessError('Name and positive USD price required')
    store.execute('''INSERT INTO Product (product_id,name,category,description,image_url,brand,is_simulated) VALUES (%s,%s,%s,%s,%s,'Moroccanoil',1)
        ON DUPLICATE KEY UPDATE name=VALUES(name),category=VALUES(category),description=VALUES(description),image_url=VALUES(image_url)''',
        (key,data['name'],data.get('category','treatment'),data.get('description',''),data.get('image_url','')))
    if data.get('variant_id'):
        store.execute('UPDATE Product_Variant SET price=%s,size=%s,is_active=%s WHERE variant_id=%s AND product_id=%s',
            (money(data['price']),data.get('size','Full'),bool(data.get('is_active',True)),data['variant_id'],key))
    else:
        store.execute('INSERT INTO Product_Variant (product_id,sku_code,variant_name,size,price) VALUES (%s,%s,%s,%s,%s)',
            (key,'HC-'+secrets.token_hex(6),data.get('size','Full'),data.get('size','Full'),money(data['price'])))
    return jsonify(product_id=key)

@app.post('/api/admin/inventory')
@endpoint('admin')
def inventory(store,user):
    store.inventory(user,request.get_json() or {})
    return jsonify(message='Inventory updated')

@app.route('/api/admin/<resource>',methods=['GET','POST'])
@endpoint('admin')
def resources(store,user,resource):
    data=request.get_json(silent=True) or {}
    if resource=='settings':
        if request.method=='POST':
            for key in ('shipping_fee','lead_time_days','review_period_days','safety_stock','low_stock_threshold'):
                if key in data:
                    value=money(data[key])
                    if value<0 or (key!='shipping_fee' and value!=int(value)): raise BusinessError('Settings must be nonnegative; days and stock must be integers')
                    store.execute('INSERT INTO Settings (setting_key,setting_value) VALUES (%s,%s) ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value)',(key,str(value)))
        return jsonify(settings={r['setting_key']:r['setting_value'] for r in store.all('SELECT * FROM Settings')})
    if resource=='vouchers':
        if request.method=='POST':
            if not data.get('code') or data.get('discount_type') not in ('percentage','fixed') or money(data.get('discount_value',0))<=0: raise BusinessError('Valid code, type and positive value required')
            if data['discount_type']=='percentage' and money(data['discount_value'])>100: raise BusinessError('Percentage cannot exceed 100')
            store.execute('''INSERT INTO Voucher (code,discount_type,discount_value,minimum_order_amount,usage_limit,start_at,end_at,status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE discount_type=VALUES(discount_type),discount_value=VALUES(discount_value),
                minimum_order_amount=VALUES(minimum_order_amount),usage_limit=VALUES(usage_limit),start_at=VALUES(start_at),end_at=VALUES(end_at),status=VALUES(status)''',
                (data['code'].upper(),data['discount_type'],money(data['discount_value']),money(data.get('minimum_order_amount',0)),
                int(data['usage_limit']) if data.get('usage_limit') else None,data.get('start_at') or None,data.get('end_at') or None,data.get('status','active')))
        return jsonify(vouchers=store.all('SELECT * FROM Voucher'))
    if resource=='customers':
        if request.method=='POST':
            if data.get('status') not in ('active','locked'): raise BusinessError('Invalid account status')
            store.execute('UPDATE `User` SET status=%s WHERE user_id=%s',(data['status'],data['user_id']))
        return jsonify(customers=store.all('SELECT user_id,email,full_name,phone,status FROM `User`'))
    if resource=='reviews':
        if request.method=='POST':
            if data.get('status') not in ('published','hidden'): raise BusinessError('Invalid review status')
            store.execute('UPDATE Review SET status=%s WHERE review_id=%s',(data['status'],data['review_id']))
        return jsonify(reviews=store.all('SELECT * FROM Review ORDER BY created_at DESC'))
    raise BusinessError('Resource not found',404)

@app.get('/api/admin/dashboard')
@endpoint('admin')
def dashboard(store,user):
    from api.reporting import dashboard_data
    start=request.args.get('start',(datetime.now()-timedelta(days=29)).date().isoformat())
    end=request.args.get('end',datetime.now().date().isoformat())
    return jsonify(dashboard_data(store.orders(user),store.products(True),start,end))

@app.route('/api/admin/ml',methods=['GET','POST'])
@endpoint('admin')
def ml(store,user):
    from api.forecasting import report,run_inference
    return jsonify(run_inference(store) if request.method=='POST' else report(store))

@app.get('/api/admin/history')
@endpoint('admin')
def history(store,user):
    from api.analytics import from_store
    return jsonify(from_store(store))

if __name__=='__main__':
    raise SystemExit('Start the complete Admin application with: python main.py')
