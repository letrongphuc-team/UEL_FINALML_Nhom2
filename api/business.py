"""Shared, deterministic commerce rules. Money is always Decimal USD."""
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime


class BusinessError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def money(value):
    try:
        result = Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        if not result.is_finite():
            raise ValueError()
        return result
    except Exception:
        raise BusinessError('Invalid monetary amount')


def positive_int(value):
    if isinstance(value, bool) or not str(value).isdigit() or int(value) < 1:
        raise BusinessError('Quantity must be a positive integer')
    return int(value)


def quote(lines, voucher=None, settings=None):
    settings = settings or {}
    subtotal = sum((money(x['price']) * positive_int(x['quantity']) for x in lines), Decimal(0))
    if not lines:
        raise BusinessError('Cart is empty')
    discount = Decimal(0)
    if voucher:
        now = datetime.now()
        if voucher['status'] != 'active':
            raise BusinessError('Voucher is disabled')
        for key, expired in [('start_at', False), ('end_at', True)]:
            date = voucher.get(key)
            if isinstance(date, str):
                date = datetime.fromisoformat(date.replace('Z', '+00:00')).replace(tzinfo=None)
            if date and ((expired and now > date) or (not expired and now < date)):
                raise BusinessError('Voucher is outside its validity period')
        if subtotal < money(voucher.get('minimum_order_amount', 0)):
            raise BusinessError('Minimum order amount not reached')
        if voucher.get('usage_limit') is not None and voucher.get('uses', 0) >= voucher['usage_limit']:
            raise BusinessError('Voucher usage limit reached')
        value = money(voucher['discount_value'])
        discount = money(subtotal * value / 100) if voucher['discount_type'] == 'percentage' else value
        discount = min(subtotal, discount)
    shipping = money(settings.get('shipping_fee', '5'))
    return dict(subtotal=subtotal, discount=discount, shipping_fee=shipping,
                total=money(subtotal - discount + shipping))


TRANSITIONS = {
    'payment_pending': {'processing', 'cancelled'},
    'processing': {'shipping', 'cancelled'},
    'shipping': {'completed'},
    'completed': {'return_pending'},
    'return_pending': {'returned', 'completed'},
}


def check_transition(current, target):
    if target not in TRANSITIONS.get(current, set()):
        raise BusinessError(f'Cannot change {current} to {target}', 409)


def recommended_order(demand, safety_stock, on_hand, reserved, incoming):
    return max(0, float(demand) + float(safety_stock) - (on_hand - reserved + incoming))
