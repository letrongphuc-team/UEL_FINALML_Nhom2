/* Administrative views for products, inventory, orders and store settings. */
(() => {
  'use strict';
  const api=(path,body)=>window.FOLMARE_ML.api('admin/'+path,body);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt=value=>Number(value||0).toLocaleString('en-GB',{maximumFractionDigits:2});
  const money=(value,currency='USD')=>Number(value||0).toLocaleString('en-GB',{style:'currency',currency});
  const date=value=>value?new Date(value).toLocaleString():'—';
  const category=value=>({shampoo:'Shampoo',conditioner:'Conditioner',hairmask:'Hair Masks',treatment:'Treatment / Oil',hairstyling:'Styling & Finishing','dry shampoo':'Dry Shampoo'}[value]||value);
  const catOptions=['shampoo','conditioner','hairmask','treatment','hairstyling','dry shampoo'];
  const title={products:'Products',inventory:'Inventory',orders:'Orders',vouchers:'Discount codes',customers:'Customers',reviews:'Reviews',settings:'Settings'};
  let cleanup=()=>{};
  async function mount(kind,id,root,helpers){
    cleanup();let disposed=false,rows=[],page=1,query='';const $=s=>root.querySelector(s);
    const live=()=>!disposed&&root.isConnected;
    cleanup=()=>{disposed=true;root.removeEventListener('click',onClick);};
    root.innerHTML=`<div class="view ml-view"><header class="ml-heading"><div><h1>${esc(title[kind]||'Page not found')}</h1></div><button type="button" aria-label="Toggle light and dark mode" title="Toggle light and dark mode" class="btn gh theme-toggle"><svg viewbox="0 0 24 24" aria-hidden="true"><path d="M20 13a8 8 0 0 1-9-9 8 8 0 1 0 9 9Z"></path></svg></button></header><div data-content><p role="status">Loading…</p></div></div>`;
    const fail=error=>{if(live())$('[data-content]').innerHTML=`<div class="card ml-error" role="alert"><p>${esc(error.message)}</p><button class="btn" data-action="retry">Try again</button></div>`;};
    root.addEventListener('click',onClick);
    try{await load();}catch(error){fail(error);}
    async function load(){
      if(!title[kind]){$('[data-content]').innerHTML='<p>Page not found.</p>';return;}
      if(kind==='settings'){const result=await api('settings');if(live())drawSettings(result.settings);return;}
      if(kind==='orders'&&id){const order=await api('orders/'+encodeURIComponent(id));if(live())drawOrder(order);return;}
      if(kind==='products'&&id){const result=await api('products');if(live())drawProduct(id==='new'?null:result.products.find(p=>String(p.product_id)===id));return;}
      const resource=kind==='inventory'?'products':kind;
      const result=await api(resource);if(!live())return;rows=result[resource]||[];drawList();
    }
    function toolbar(){return `<div class="ml-controls"><label>Search ${title[kind]}<input aria-label="Search records" type="search" placeholder="Search records…" value="${esc(query)}"></label>${kind==='products'?'<a class="btn" href="#/admin/products/new">Add product</a>':kind==='vouchers'?'<button class="btn" data-action="voucher-new">Add voucher</button>':''}<span class="muted">${rows.length} records</span></div>`;}
    function drawList(){
      const filtered=rows.filter(r=>JSON.stringify(r).toLowerCase().includes(query.toLowerCase()));const totalPages=Math.max(1,Math.ceil(filtered.length/20));page=Math.max(1,Math.min(page,totalPages));
      const subset=filtered.slice((page-1)*20,page*20);
      const headers={products:['Product','Category','SKU / Size','Price','Active',''],inventory:['Product / SKU','On hand','Reserved','Available','Incoming',''],orders:['Order','Date','Customer','Total','Status',''],vouchers:['Code','Type','Discount','Minimum','Status',''],customers:['Name','Email','Phone','Status',''],reviews:['Product','Rating','Title / Content','Status','']}[kind];
      const body=subset.map(r=>{
        if(kind==='products')return `<tr><td class="wrap"><b>${esc(r.name)}</b></td><td>${esc(category(r.category))}</td><td>${esc(r.sku_code)}<br><small>${esc(r.size)}</small></td><td>${money(r.price,r.currency)}</td><td>${r.is_active?'Active':'Inactive'}</td><td><a class="btn sm gh" href="#/admin/products/${encodeURIComponent(r.product_id)}/edit">Edit</a></td></tr>`;
        if(kind==='inventory')return `<tr><td class="wrap"><b>${esc(r.name)}</b><br><small>${esc(r.sku_code)} · ${esc(r.size)}</small></td><td>${r.on_hand}</td><td>${r.reserved}</td><td>${r.on_hand-r.reserved}</td><td>${r.incoming}</td><td><div class="ml-row-actions"><button class="btn sm gh" data-action="stock-adjust" data-id="${r.variant_id}">Adjust stock</button><button class="btn sm" data-action="stock-order" data-id="${r.variant_id}">Order Stock</button><button class="btn sm gh" data-action="stock-receive" data-id="${r.variant_id}" ${r.incoming?'':'disabled'}>Receive Stock</button></div></td></tr>`;
        if(kind==='orders')return `<tr><td>#${r.order_id}</td><td>${date(r.created_at)}</td><td>${esc(r.customer_name||'—')}</td><td>${money(r.order_total,r.currency)}</td><td><span class="b">${esc(r.status)}</span></td><td><a href="#/admin/orders/${r.order_id}" class="btn sm gh">View</a></td></tr>`;
        if(kind==='vouchers')return `<tr><td><b>${esc(r.code)}</b></td><td>${esc(r.discount_type)}</td><td>${r.discount_type==='percentage'?fmt(r.discount_value)+'%':money(r.discount_value)}</td><td>${money(r.minimum_order_amount)}</td><td>${esc(r.status)}</td><td><button class="btn sm gh" data-action="voucher-edit" data-id="${r.voucher_id}">Edit</button></td></tr>`;
        if(kind==='customers')return `<tr><td>${esc(r.full_name||'—')}</td><td>${esc(r.email)}</td><td>${esc(r.phone||'—')}</td><td>${esc(r.status)}</td><td><button class="btn sm gh" data-action="customer-status" data-id="${r.user_id}">${r.status==='locked'?'Unlock account':'Lock account'}</button></td></tr>`;
        return `<tr><td>${esc(r.product_id)}</td><td>${r.rating} / 5</td><td class="wrap">${esc(r.title)}<br>${esc(r.content)}</td><td>${esc(r.status)}</td><td><button class="btn sm gh" data-action="review-status" data-id="${r.review_id}">${r.status==='hidden'?'Publish':'Hide'}</button></td></tr>`;
      }).join('');
      $('[data-content]').innerHTML=`<div class="card">${toolbar()}<div class="tw"><table><thead><tr>${headers.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${body||`<tr><td colspan="${headers.length}" class="local-empty">No matching records.</td></tr>`}</tbody></table></div><div class="ml-pager"><span>${filtered.length} results · page ${page} / ${totalPages}</span><div><button class="btn gh sm" data-action="previous" ${page===1?'disabled':''}>Previous</button> <button class="btn gh sm" data-action="next" ${page===totalPages?'disabled':''}>Next</button></div></div></div>`;
      $('[aria-label="Search records"]').addEventListener('input',event=>{const cursor=event.target.selectionStart;query=event.target.value;page=1;drawList();const input=$('[aria-label="Search records"]');input.focus();input.setSelectionRange(cursor,cursor);});
    }
    function drawProduct(product){
      if(id!=='new'&&!product)throw new Error('Product not found.');
      const p=product||{};
      $('[data-content]').innerHTML=`<div class="card"><a href="#/admin/products" class="back">← Products</a><h3>${p.product_id?'Edit product':'Add product'}</h3><form data-product-form class="ml-admin-form"><label>Product name<input name="name" required value="${esc(p.name)}"></label><label>Category<select name="category">${catOptions.map(c=>`<option value="${c}" ${p.category===c?'selected':''}>${category(c)}</option>`).join('')}</select></label><label>Description<textarea name="description" rows="5">${esc(p.description)}</textarea></label><label>Price · USD<input name="price" type="number" step="0.01" min="0.01" required value="${esc(p.price)}"></label><label>Size<input name="size" required value="${esc(p.size)}"></label><label>Image URL<input name="image_url" type="url" value="${esc(p.image_url)}"></label><label class="ml-checkbox"><input name="active" type="checkbox" ${p.is_active||!p.product_id?'checked':''}>Active variant</label><div role="alert" class="local-message"></div><button class="btn" type="submit">Save product</button></form></div>`;
      $('[data-product-form]').onsubmit=async event=>{event.preventDefault();const form=event.currentTarget;if(!form.reportValidity())return;const data=new FormData(form),button=form.querySelector('button[type=submit]');button.disabled=true;
        try{await api('products',{...(p.product_id?{product_id:p.product_id,variant_id:p.variant_id}:{}),name:data.get('name'),category:data.get('category'),description:data.get('description'),price:Number(data.get('price')),size:data.get('size'),image_url:data.get('image_url'),is_active:data.has('active')});helpers.go('/admin/products');helpers.toast('Product saved.');}catch(error){form.querySelector('[role=alert]').textContent=error.message;button.disabled=false;}};
    }
    function drawSettings(settings){
      const labels={shipping_fee:'Shipping fee · USD',lead_time_days:'Lead time · days',review_period_days:'Review period · days',safety_stock:'Minimum safety stock · units',service_level_pct:'Service level · % (0 = fixed safety stock only)',low_stock_threshold:'Low-stock threshold · units'};
      $('[data-content]').innerHTML=`<div class="card"><h3>Store settings</h3><p class="local-note">Store currency: ${esc(settings.currency)} · Forecast currency: ${esc(settings.forecast_currency)}</p><form data-settings-form class="ml-admin-form">${Object.entries(labels).map(([key,label])=>`<label>${label}<input type="number" name="${key}" min="0" step="${key==='shipping_fee'?'0.01':'1'}" value="${esc(settings[key])}" required></label>`).join('')}<div class="local-message" role="alert"></div><button class="btn" type="submit">Save changes</button></form></div>`;
      $('[data-settings-form]').onsubmit=async event=>{event.preventDefault();const form=event.currentTarget;if(!form.reportValidity())return;const button=form.querySelector('button');button.disabled=true;try{await api('settings',Object.fromEntries([...new FormData(form)].map(([k,v])=>[k,Number(v)])));helpers.toast('Settings saved.');}catch(error){form.querySelector('[role=alert]').textContent=error.message;}finally{button.disabled=false;}};
    }
    function drawOrder(order){
      const next={payment_pending:['cancelled'],processing:['shipping','cancelled'],shipping:['completed'],return_pending:['returned','completed']}[order.status]||[];
      $('[data-content]').innerHTML=`<div class="card"><a href="#/admin/orders" class="back">← Orders</a><h3>Order #${order.order_id} <em>${esc(order.status)}</em></h3><p>${esc(order.customer_name||'—')} · ${esc(order.phone||'—')}</p><p>${esc(order.shipping_address||'—')}</p><div class="tw"><table><thead><tr><th>Product</th><th>Variant</th><th>Quantity</th><th>Unit price</th><th>Total</th></tr></thead><tbody>${order.items.map(r=>`<tr><td>${esc(r.product_name_snapshot)}</td><td>${esc(r.variant_name_snapshot)}</td><td>${r.quantity}</td><td>${money(r.unit_price_snapshot,order.currency)}</td><td>${money(r.line_total_snapshot,order.currency)}</td></tr>`).join('')}</tbody></table></div><p><b>Order total: ${money(order.order_total,order.currency)}</b></p><div class="ml-actions">${next.map(status=>`<button class="btn gh" data-action="order-status" data-status="${status}" data-id="${order.order_id}">${esc(status)}</button>`).join('')}</div></div>`;
    }
    async function saveModal(path,body){const button=document.querySelector('[data-confirm]');button.disabled=true;try{await api(path,body);helpers.closeModal();if(live()){await load();helpers.toast('Changes saved.');}}catch(error){helpers.modalError(error.message);button.disabled=false;}}
    function onClick(event){
      const button=event.target.closest('[data-action]');if(!button||button.disabled)return;event.stopPropagation();const action=button.dataset.action,key=button.dataset.id;
      if(action==='retry'){load().catch(fail);return;}
      if(action==='next'||action==='previous'){page+=action==='next'?1:-1;drawList();return;}
      if(action.startsWith('stock-')){
        const item=rows.find(r=>String(r.variant_id)===key),operation=action.slice(6);
        helpers.openModal(operation==='adjust'?'Adjust stock':operation==='order'?'Order Stock':'Receive Stock',`<p>${esc(item.name)} · ${esc(item.sku_code)}</p><label>Quantity<input id="admin-stock-quantity" type="number" step="1" min="${operation==='adjust'?item.reserved:1}" ${operation==='receive'?`max="${item.incoming}"`:''} value="${operation==='adjust'?item.on_hand:operation==='receive'?item.incoming:1}"></label><label>Reason<input id="admin-stock-reason" required></label>`,'Save',()=>{const quantity=document.querySelector('#admin-stock-quantity'),reason=document.querySelector('#admin-stock-reason');if(!quantity.reportValidity()||!reason.reportValidity())return;saveModal('inventory',{variant_id:item.variant_id,action:operation,quantity:Number(quantity.value),reason:reason.value});});return;
      }
      if(action==='customer-status'){
        const item=rows.find(r=>String(r.user_id)===key),status=item.status==='locked'?'active':'locked';helpers.openModal('Update customer account',`<p>${esc(item.email)}</p><p>Set account status to ${status}?</p>`,'Save',()=>saveModal('customers',{user_id:item.user_id,status}));return;
      }
      if(action==='review-status'){
        const item=rows.find(r=>String(r.review_id)===key);helpers.openModal('Review visibility',`<p>${esc(item.content)}</p>`,'Save',()=>saveModal('reviews',{review_id:item.review_id,status:item.status==='hidden'?'published':'hidden'}));return;
      }
      if(action==='order-status'){
        helpers.openModal('Update order status',`<p>Order #${esc(key)} → ${esc(button.dataset.status)}</p><label>Reason<textarea id="order-reason"></textarea></label>`,'Save',()=>saveModal('orders/'+key+'/transition',{status:button.dataset.status,reason:document.querySelector('#order-reason').value}));return;
      }
      if(action==='voucher-new'||action==='voucher-edit'){
        const item=rows.find(r=>String(r.voucher_id)===key)||{};
        helpers.openModal(item.voucher_id?'Edit voucher':'Add voucher',`<label>Code<input id="voucher-code" value="${esc(item.code)}" ${item.voucher_id?'readonly':''} required></label><label>Type<select id="voucher-type"><option value="percentage">Percentage</option><option value="fixed" ${item.discount_type==='fixed'?'selected':''}>Fixed · USD</option></select></label><label>Value<input id="voucher-value" type="number" min="0.01" step="0.01" value="${esc(item.discount_value)}" required></label><label>Minimum order<input id="voucher-min" type="number" min="0" step="0.01" value="${esc(item.minimum_order_amount??0)}"></label><label>Usage limit<input id="voucher-limit" type="number" min="1" step="1" value="${esc(item.usage_limit)}"></label><label>Status<select id="voucher-status"><option value="active">Active</option><option value="inactive" ${item.status==='inactive'?'selected':''}>Inactive</option></select></label>`,'Save',()=>{const get=s=>document.querySelector(s);if(!get('#voucher-code').reportValidity()||!get('#voucher-value').reportValidity()||!get('#voucher-min').reportValidity()||!get('#voucher-limit').reportValidity())return;saveModal('vouchers',{code:get('#voucher-code').value,discount_type:get('#voucher-type').value,discount_value:Number(get('#voucher-value').value),minimum_order_amount:Number(get('#voucher-min').value),usage_limit:get('#voucher-limit').value?Number(get('#voucher-limit').value):null,status:get('#voucher-status').value,start_at:item.start_at,end_at:item.end_at});});
      }
    }
  }
  window.FOLMARE_ADMIN={mount,dispose:()=>cleanup()};
})();
