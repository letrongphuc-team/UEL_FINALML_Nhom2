from decimal import Decimal
import pytest
from api.business import quote,BusinessError,recommended_order,positive_int,check_transition
from api.reporting import dashboard_data

def test_quote_uses_decimal_and_fixed_shipping():
    totals=quote([{'price':'0.10','quantity':3},{'price':'29.90','quantity':1}],
        {'status':'active','discount_type':'percentage','discount_value':10,'minimum_order_amount':30})
    assert totals==dict(subtotal=Decimal('30.20'),discount=Decimal('3.02'),shipping_fee=Decimal('5.00'),total=Decimal('32.18'))

@pytest.mark.parametrize('value',[True,0,-1,'1.5','bad'])
def test_quantities_reject_invalid(value):
    with pytest.raises(BusinessError):positive_int(value)

def test_state_machine_and_inventory_formula():
    with pytest.raises(BusinessError):check_transition('processing','completed')
    assert recommended_order(100,15,60,10,20)==45

def test_dashboard_completion_dates_returns_and_currency():
    orders=[dict(status=s,completed_at='2026-10-30T12:00:00',subtotal=100,discount_total=10) for s in ['completed','return_pending','returned','processing']]
    result=dashboard_data(orders,[],'2026-10-30','2026-10-30')
    assert result['totals']==dict(net_revenue=180,completed_orders=2,aov=90)
    assert result['currency']=='USD' and result['previous_start']=='2026-10-29'
