"""MySQL repository. A request owns one transaction, including stock reservations."""
from contextlib import contextmanager
from datetime import datetime, timedelta
from uuid import uuid4
from decimal import Decimal
import json
from werkzeug.security import generate_password_hash, check_password_hash
from connector.connector import Connector
from api.business import BusinessError, quote, money, positive_int, check_transition


class Store:
    def __init__(self, connection):
        self.conn = connection

    def all(self, sql, params=()):
        cursor = self.conn.cursor(dictionary=True)
        try:
            cursor.execute(sql, params)
            return cursor.fetchall()
        finally:
            cursor.close()

    def one(self, sql, params=()):
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def execute(self, sql, params=()):
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, params)
            return cursor.lastrowid
        finally:
            cursor.close()

    def execute_many(self, sql, rows):
        if not rows:
            return
        cursor = self.conn.cursor()
        try:
            cursor.executemany(sql, rows)
        finally:
            cursor.close()

    def products(self, admin=False):
        return self.all('''SELECT p.*, pv.variant_id, pv.sku_code, pv.variant_name,
            pv.size, pv.price, pv.currency, pv.on_hand, pv.reserved, pv.incoming, pv.is_active
            FROM Product p JOIN Product_Variant pv ON p.product_id=pv.product_id ''' +
            ('' if admin else 'WHERE pv.is_active=1 ') + 'ORDER BY p.name, pv.variant_id')

    def register(self, payload):
        email = str(payload.get('email', '')).strip().lower()
        password = str(payload.get('password', ''))
        name = str(payload.get('name', '')).strip()
        if '@' not in email or len(password) < 8 or not name:
            raise BusinessError('Name, valid email and password of at least 8 characters required')
        if self.one('SELECT user_id FROM `User` WHERE email=%s FOR UPDATE', (email,)):
            raise BusinessError('Email already registered', 409)
        user_id = self.execute('INSERT INTO `User` (email,password_hash,full_name,status) VALUES (%s,%s,%s,%s)',
                              (email, generate_password_hash(password), name, 'active'))
        return dict(user_id=user_id, email=email, name=name, role='customer')

    def login(self, payload):
        role = 'admin' if payload.get('role') == 'admin' else 'customer'
        table, key = ('Admin_User', 'admin_user_id') if role == 'admin' else ('User', 'user_id')
        user = self.one(f'SELECT * FROM `{table}` WHERE email=%s', (str(payload.get('email', '')).strip().lower(),))
        if not user or user['status'] != 'active' or not user['password_hash'] or not check_password_hash(user['password_hash'], str(payload.get('password', ''))):
            raise BusinessError('Incorrect email or password', 401)
        return dict(user_id=user[key], email=user['email'], name=user['full_name'], role=role)

    def validate_user(self, user):
        table, key = ('Admin_User', 'admin_user_id') if user['role'] == 'admin' else ('User', 'user_id')
        row = self.one(f'SELECT status FROM `{table}` WHERE `{key}`=%s', (user['user_id'],))
        if not row or row['status'] != 'active':
            raise BusinessError('Account is unavailable', 401)

    def cart(self, user_id, payload=None):
        cart = self.one("SELECT cart_id FROM Cart WHERE user_id=%s AND status='active' ORDER BY cart_id LIMIT 1 FOR UPDATE", (user_id,))
        cart_id = cart['cart_id'] if cart else self.execute("INSERT INTO Cart (user_id,status) VALUES (%s,'active')", (user_id,))
        if payload is not None:
            self.execute('DELETE FROM Cart_Item WHERE cart_id=%s', (cart_id,))
            quantities = {}
            for item in payload.get('items', []):
                variant = positive_int(item['variant_id'])
                quantities[variant] = quantities.get(variant, 0) + positive_int(item['quantity'])
            for variant, quantity in quantities.items():
                if not self.one('SELECT variant_id FROM Product_Variant WHERE variant_id=%s', (variant,)):
                    raise BusinessError('Unknown variant')
                self.execute('INSERT INTO Cart_Item (cart_id,variant_id,quantity) VALUES (%s,%s,%s)', (cart_id,variant,quantity))
        return self.all('SELECT variant_id,quantity FROM Cart_Item WHERE cart_id=%s', (cart_id,))

    def lines(self, items):
        quantities = {}
        for item in items:
            key = positive_int(item.get('variant_id'))
            quantities[key] = quantities.get(key, 0) + positive_int(item.get('quantity'))
        lines = []
        # Stable lock order prevents two multi-item checkouts from deadlocking.
        for key in sorted(quantities):
            row = self.one('''SELECT pv.*,p.name FROM Product_Variant pv JOIN Product p
                ON p.product_id=pv.product_id WHERE pv.variant_id=%s FOR UPDATE''', (key,))
            if not row or not row['is_active'] or row['on_hand'] - row['reserved'] < quantities[key]:
                raise BusinessError('Product unavailable or insufficient stock', 409)
            row['quantity'] = quantities[key]
            lines.append(row)
        return lines

    def get_quote(self, payload):
        lines = self.lines(payload.get('items', []))
        voucher = None
        if payload.get('voucher'):
            voucher = self.one('SELECT * FROM Voucher WHERE code=%s FOR UPDATE', (str(payload['voucher']).upper(),))
            if not voucher:
                raise BusinessError('Voucher not found')
            voucher['uses'] = self.one('SELECT COUNT(*) AS n FROM Voucher_Redemption WHERE voucher_id=%s', (voucher['voucher_id'],))['n']
        settings = {r['setting_key']: r['setting_value'] for r in self.all('SELECT * FROM Settings')}
        return lines, voucher, quote(lines, voucher, settings)

    def checkout(self, user, payload):
        key = str(payload.get('checkout_key', ''))
        if not key or len(key) > 100:
            raise BusinessError('A checkout key is required')
        # Lock user to serialize identical retries before the unique insert.
        self.one('SELECT user_id FROM `User` WHERE user_id=%s FOR UPDATE', (user['user_id'],))
        existing = self.one('SELECT order_id FROM `Order` WHERE user_id=%s AND checkout_key=%s', (user['user_id'], key))
        if existing:
            return self.order(existing['order_id'], user)
        if not all(str(payload.get(k, '')).strip() for k in ('customer_name', 'phone', 'address')):
            raise BusinessError('Name, phone and shipping address are required')
        method = payload.get('payment_method', 'COD')
        if method not in ('COD', 'Bank Transfer'):
            raise BusinessError('Unsupported payment method')
        lines, voucher, totals = self.get_quote(payload)
        status = 'processing' if method == 'COD' else 'payment_pending'
        reference = 'PAY-HCI-' + uuid4().hex[:16].upper() if method != 'COD' else None
        expires = datetime.now() + timedelta(minutes=30) if reference else None
        order_id = self.execute('''INSERT INTO `Order` (user_id,status,subtotal,discount_total,shipping_fee,order_total,currency,
            checkout_key,customer_name,phone,shipping_address,payment_reference,expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,'USD',%s,%s,%s,%s,%s,%s)''',
            (user['user_id'],status,totals['subtotal'],totals['discount'],totals['shipping_fee'],totals['total'],key,
             payload['customer_name'],payload['phone'],payload['address'],reference,expires))
        for row in lines:
            self.execute('''INSERT INTO Order_Item (order_id,variant_id,quantity,unit_price_snapshot,line_total_snapshot,
                product_name_snapshot,variant_name_snapshot) VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                (order_id,row['variant_id'],row['quantity'],row['price'],row['quantity']*row['price'],row['name'],row['size']))
            self.execute('UPDATE Product_Variant SET reserved=reserved+%s WHERE variant_id=%s', (row['quantity'], row['variant_id']))
        self.execute("INSERT INTO Payment (order_id,provider,amount,currency,status) VALUES (%s,%s,%s,'USD','pending')", (order_id,method,totals['total']))
        if voucher:
            self.execute('INSERT INTO Voucher_Redemption (voucher_id,user_id,order_id,discount_amount) VALUES (%s,%s,%s,%s)',
                         (voucher['voucher_id'],user['user_id'],order_id,totals['discount']))
        self.cart(user['user_id'], {'items': []})
        return self.order(order_id, user)

    def order(self, order_id, user):
        row = self.one('SELECT * FROM `Order` WHERE order_id=%s FOR UPDATE', (order_id,))
        if not row or (user['role'] != 'admin' and row['user_id'] != user['user_id']):
            raise BusinessError('Order not found', 404)
        row['items'] = self.all('SELECT * FROM Order_Item WHERE order_id=%s ORDER BY variant_id', (order_id,))
        row['payments'] = self.all('SELECT * FROM Payment WHERE order_id=%s', (order_id,))
        return row

    def orders(self, user):
        query = 'SELECT * FROM `Order`'
        rows=self.all(query + (' ORDER BY created_at DESC' if user['role'] == 'admin' else ' WHERE user_id=%s ORDER BY created_at DESC'),
                        () if user['role']=='admin' else (user['user_id'],))
        for row in rows:
            row['items']=self.all('SELECT * FROM Order_Item WHERE order_id=%s ORDER BY variant_id',(row['order_id'],))
        return rows

    def transition(self, order_id, user, target, reason=''):
        row = self.order(order_id, user)
        current = row['status']
        check_transition(current, target)
        if user['role'] != 'admin' and target not in ('cancelled', 'return_pending'):
            raise BusinessError('Admin permission required', 403)
        if current == 'payment_pending' and target == 'processing':
            raise BusinessError('Use payment confirmation to process this order')
        if target in ('cancelled', 'return_pending') and not str(reason).strip():
            raise BusinessError('A reason is required')
        if target == 'return_pending':
            if not row['completed_at'] or datetime.now() > row['completed_at'] + timedelta(days=7):
                raise BusinessError('Returns must be requested within 7 days of completion')
            return_id = self.execute('INSERT INTO Return_Request (order_id,user_id,reason) VALUES (%s,%s,%s)', (order_id,row['user_id'],reason))
            for item in row['items']:
                self.execute('INSERT INTO Return_Item (return_id,order_item_id,quantity,refund_amount) VALUES (%s,%s,%s,%s)',
                             (return_id,item['order_item_id'],item['quantity'],item['line_total_snapshot']))
        for item in row['items']:
            quantity, variant = item['quantity'], item['variant_id']
            if target == 'cancelled':
                self.execute('UPDATE Product_Variant SET reserved=reserved-%s WHERE variant_id=%s', (quantity,variant))
            elif target == 'shipping':
                self.execute('UPDATE Product_Variant SET reserved=reserved-%s,on_hand=on_hand-%s WHERE variant_id=%s', (quantity,quantity,variant))
            elif target == 'returned':
                self.execute('UPDATE Product_Variant SET on_hand=on_hand+%s WHERE variant_id=%s', (quantity,variant))
            if target in ('shipping', 'returned'):
                self.execute('''INSERT INTO Inventory_Adjustment (variant_id,admin_user_id,adjustment_type,quantity_delta,reason,reference_type,reference_id)
                    VALUES (%s,%s,%s,%s,%s,'Order',%s)''', (variant,user['user_id'], 'sale' if target=='shipping' else 'return',
                     -quantity if target=='shipping' else quantity, reason or target,order_id))
        self.execute('UPDATE `Order` SET status=%s WHERE order_id=%s', (target,order_id))
        if target == 'completed' and current == 'shipping':
            self.execute('UPDATE `Order` SET completed_at=NOW() WHERE order_id=%s', (order_id,))
            self.execute("UPDATE Payment SET status='paid',paid_at=NOW() WHERE order_id=%s AND provider='COD'", (order_id,))
        if target in ('cancelled', 'returned'):
            self.execute("UPDATE Payment SET status=CASE WHEN status='paid' THEN 'refunded' ELSE 'failed' END WHERE order_id=%s", (order_id,))
        if target == 'cancelled':
            self.execute('UPDATE `Order` SET cancellation_reason=%s,cancelled_at=NOW() WHERE order_id=%s', (reason,order_id))
        if current == 'return_pending':
            self.execute('UPDATE Return_Request SET status=%s,resolved_at=NOW() WHERE order_id=%s AND status=%s',
                         ('completed' if target=='returned' else 'rejected',order_id,'requested'))
        return self.order(order_id, user)

    def expire(self):
        # Expiration values are created with Python local time; compare using that same clock.
        rows = self.all("SELECT order_id FROM `Order` WHERE status='payment_pending' AND expires_at<%s ORDER BY order_id FOR UPDATE", (datetime.now(),))
        for row in rows:
            self.transition(row['order_id'], {'role':'admin','user_id':None}, 'cancelled', 'Payment expired after 30 minutes')

    def pay(self, order_id, user, payload):
        row = self.order(order_id, user)
        if not row['payment_reference'] or payload.get('reference') != row['payment_reference'] or money(payload.get('amount', -1)) != row['order_total']:
            raise BusinessError('Payment reference or USD amount does not match')
        if any(p['status']=='paid' for p in row['payments']):
            return row
        if row['status'] != 'payment_pending' or row['expires_at'] < datetime.now():
            raise BusinessError('Payment is expired or order is not awaiting payment',409)
        if payload.get('outcome') == 'failed':
            return self.transition(order_id, {'role':'admin','user_id':None}, 'cancelled', 'Simulated payment failed')
        self.execute("UPDATE Payment SET status='paid',paid_at=NOW(),provider_transaction_id=%s WHERE order_id=%s", (row['payment_reference'],order_id))
        self.execute("UPDATE `Order` SET status='processing',paid_at=NOW() WHERE order_id=%s", (order_id,))
        return self.order(order_id,user)

    def inventory(self, user, payload):
        variant = positive_int(payload.get('variant_id'))
        row = self.one('SELECT * FROM Product_Variant WHERE variant_id=%s FOR UPDATE', (variant,))
        if not row:
            raise BusinessError('Variant not found',404)
        action = payload.get('action')
        quantity = positive_int(payload.get('quantity')) if action != 'adjust' else int(payload.get('quantity',-1))
        if not payload.get('reason'):
            raise BusinessError('Inventory reason required')
        delta = 0
        if action == 'order':
            self.execute('UPDATE Product_Variant SET incoming=incoming+%s WHERE variant_id=%s', (quantity,variant))
        elif action == 'receive':
            if quantity > row['incoming']:
                raise BusinessError('Cannot receive more than incoming stock')
            delta = quantity
            self.execute('UPDATE Product_Variant SET incoming=incoming-%s,on_hand=on_hand+%s WHERE variant_id=%s',(quantity,quantity,variant))
        elif action == 'adjust':
            if quantity < row['reserved']:
                raise BusinessError('On hand cannot be lower than reserved')
            delta = quantity-row['on_hand']
            self.execute('UPDATE Product_Variant SET on_hand=%s WHERE variant_id=%s',(quantity,variant))
        else:
            raise BusinessError('Unknown inventory action')
        self.execute('''INSERT INTO Inventory_Adjustment (variant_id,admin_user_id,adjustment_type,quantity_delta,reason)
            VALUES (%s,%s,%s,%s,%s)''',(variant,user['user_id'],action,delta,payload['reason']))


@contextmanager
def transaction():
    db = Connector()
    try:
        connection = db.connect()
        connection.start_transaction()
        yield Store(connection)
        connection.commit()
    except Exception:
        if db.conn:
            db.conn.rollback()
        raise
    finally:
        db.close()
