from datetime import date,timedelta

def dashboard_data(orders,products,start,end):
    start,end=date.fromisoformat(start),date.fromisoformat(end)
    if end<start or (end-start).days>1095: raise ValueError('Invalid date range')
    days=(end-start).days+1
    def completed(a,b):
        return [o for o in orders if o['status'] in ('completed','return_pending') and o.get('completed_at')
            and a<=date.fromisoformat(str(o['completed_at'])[:10])<=b]
    def totals(rows):
        revenue=sum(float(o['subtotal'])-float(o['discount_total']) for o in rows)
        return dict(net_revenue=round(revenue,2),completed_orders=len(rows),aov=round(revenue/len(rows),2) if rows else 0)
    sellers={}
    for order in completed(start,end):
        ratio=(float(order['subtotal'])-float(order['discount_total']))/float(order['subtotal']) if float(order['subtotal']) else 0
        for item in order.get('items',[]):
            key=item['variant_id']
            row=sellers.setdefault(key,dict(name=item['product_name_snapshot'],variant_id=key,quantity=0,net_revenue=0))
            row['quantity']+=item['quantity'];row['net_revenue']+=float(item['line_total_snapshot'])*ratio
    return dict(currency='USD',start=start.isoformat(),end=end.isoformat(),
        previous_start=(start-timedelta(days=days)).isoformat(),previous_end=(start-timedelta(days=1)).isoformat(),
        totals=totals(completed(start,end)),previous=totals(completed(start-timedelta(days=days),start-timedelta(days=1))),
        series=[dict(date=(start+timedelta(days=i)).isoformat(),**totals(completed(start+timedelta(days=i),start+timedelta(days=i)))) for i in range(days)],
        pending_orders=sum(o['status'] in ('payment_pending','processing','shipping','return_pending') for o in orders),
        low_stock=[p for p in products if p['on_hand']-p['reserved']<20],
        top_products=sorted(sellers.values(),key=lambda r:r['net_revenue'],reverse=True)[:10],
        definition='Completed + Return Pending; completion date; subtotal minus voucher discount; excludes shipping; returned orders excluded retroactively.')
