/* Forecasting and inventory views backed by the MySQL API. */
(() => {
  'use strict';
  let csrf = '', cleanup = () => {};
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = (n, decimals = 0) => Number(n).toLocaleString('en-GB', {maximumFractionDigits:decimals});
  const date = value => new Date(value).toLocaleDateString('en-GB', {day:'2-digit',month:'short',year:'numeric'});
  const catName = category => ({shampoo:'Shampoo',conditioner:'Conditioner',hairmask:'Hair Masks',treatment:'Treatment / Oil',hairstyling:'Styling & Finishing','dry shampoo':'Dry Shampoo'}[category] || category);
  async function api(path, body) {
    const response = await fetch('/api/' + path, {credentials:'same-origin', headers:body ? {'Content-Type':'application/json','X-CSRF-Token':csrf} : {}, ...(body ? {method:'POST',body:JSON.stringify(body)} : {})});
    let result;
    try { result = await response.json(); } catch { throw new Error('Unable to load this page. Please try again.'); }
    if (!response.ok) throw new Error(response.status >= 500 ? 'This service is temporarily unavailable. Please try again shortly.' : (result.error || 'Unable to complete this request. Please try again.'));
    if (result.csrf) csrf = result.csrf;
    return result;
  }
  function failure(root, error, retry) {
    root.innerHTML = `<div class="card ml-error" role="alert"><h3>Unable to load this page</h3><p>${esc(error.message)}</p><button class="btn" data-retry>Try again</button> <a class="btn gh" href="#/admin/login">Sign in</a> <a class="btn gh" href="#/admin/forecast">Open forecast</a></div>`;
    root.querySelector('[data-retry]').onclick = retry;
  }
  const heading = (title, subtitle, actions='') => `<header class="ml-heading"><div><h1>${title}</h1>${subtitle ? `<p class="muted">${subtitle}</p>` : ''}</div><div class="ml-actions">${actions}</div></header>`;
  const stat = (label, value, note) => `<div class="card ml-stat"><span class="muted">${label}</span><strong>${value}</strong><small class="muted">${note}</small></div>`;

  function chart(history, series, unit, compact=false) {
    series = series.filter(row=>row.role==='champion');
    const colors = {actual:'var(--ml-actual)',champion:'var(--ml-champion)'};
    const labels = {actual:'Actual',champion:'Forecast'};
    const periods = [...new Set([...history.map(r=>r.period), ...series.map(r=>r.period)])].sort();
    const all = [...history.map(r=>r.actual), ...series.map(r=>r.forecast)];
    const max = Math.max(...all,1) * 1.12, width=compact?520:940, height=compact?260:300, left=75, right=24, top=25, bottom=48;
    const x = period => left + periods.indexOf(period) / Math.max(1,periods.length-1) * (width-left-right);
    const y = value => height-bottom-value/max*(height-top-bottom);
    let svg = '';
    for(let i=0;i<=4;i++) { const val=max*i/4;svg+=`<line x1="${left}" x2="${width-right}" y1="${y(val)}" y2="${y(val)}" class="ml-gridline"/><text x="${left-12}" y="${y(val)+4}" text-anchor="end">${fmt(val)}</text>`; }
    const every = Math.max(1, Math.ceil(periods.length/7));
    periods.forEach((p,i)=>{if(i%every===0 || i===periods.length-1)svg+=`<text x="${x(p)}" y="${height-17}" text-anchor="middle">${p.slice(5)}</text>`;});
    const firstFuture = series[0]?.period;
    if(firstFuture)svg+=`<line x1="${x(firstFuture)}" x2="${x(firstFuture)}" y1="${top}" y2="${height-bottom}" stroke="#9aa99f" stroke-dasharray="4 5"/><text x="${x(firstFuture)+6}" y="16">Forecast →</text>`;
    for(const role of ['actual','champion']) {
      const rows = role==='actual' ? history.map(r=>({period:r.period,forecast:r.actual})) : series.filter(r=>r.role===role);
      if(!rows.length)continue;
      svg+=`<polyline fill="none" stroke="${colors[role]}" stroke-width="${role==='champion'?3:2}" ${role==='champion'?'stroke-dasharray="7 5"':''} points="${rows.map(r=>`${x(r.period)},${y(r.forecast)}`).join(' ')}"/>`;
      rows.forEach(r=>svg+=`<circle cx="${x(r.period)}" cy="${y(r.forecast)}" r="3" fill="${colors[role]}"><title>${labels[role]} · ${r.period} · ${fmt(r.forecast,2)} ${unit}</title></circle>`);
    }
    return `<div class="ml-chart" tabindex="0" role="region" aria-label="Scrollable forecast chart"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Historical actuals and forecasts in ${unit}. Hover a data point for its exact value.">${svg}</svg></div><p class="ml-chart-hint">Scroll horizontally to see the full chart →</p><div class="ml-legend">${Object.entries(labels).filter(([key])=>key==='actual'?history.length:series.length).map(([key,label])=>`<span><i style="background:${colors[key]}"></i>${label}</span>`).join('')}</div>`;
  }

  async function mountDashboard(root) {
    cleanup();let disposed=false,version=0;
    cleanup=()=>{disposed=true;};
    root.innerHTML=`<div class="view ml-view">${heading('Overview','', '<button type="button" aria-label="Toggle light and dark mode" title="Toggle light and dark mode" class="btn gh theme-toggle"><svg viewbox="0 0 24 24" aria-hidden="true"><path d="M20 13a8 8 0 0 1-9-9 8 8 0 1 0 9 9Z"></path></svg></button>')}<div class="ml-dashboard-toolbar"><p class="muted">Figures exclude shipping fees and returned orders.</p><select aria-label="Overview period"><option value="7">Last 7 days</option><option value="30" selected>Last 30 days</option><option value="90">Last 90 days</option><option value="365">Last year</option></select></div><div data-overview aria-live="polite"></div></div>`;
    const panel=root.querySelector('[data-overview]'),select=root.querySelector('[aria-label="Overview period"]');
    select.onchange=load;
    await load();
    async function load() {
      const request=++version;panel.innerHTML='<p role="status">Loading overview…</p>';
      const end=new Date(),start=new Date(end);start.setDate(start.getDate()-Number(select.value)+1);
      const iso=d=>[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');
      try {
        const data=await api('admin/dashboard?'+new URLSearchParams({start:iso(start),end:iso(end)}));
        if(disposed||request!==version)return;
        const money=n=>Number(n).toLocaleString('en-US',{style:'currency',currency:data.currency});
        const change=key=>data.previous[key] ? `${data.totals[key]>=data.previous[key]?'▲':'▼'} ${fmt(Math.abs(data.totals[key]/data.previous[key]-1)*100,1)}% vs. previous period` : 'No previous-period comparison';
        const low=[...data.low_stock].sort((a,b)=>(a.on_hand-a.reserved)-(b.on_hand-b.reserved));
        const max=Math.max(...data.top_products.map(r=>r.net_revenue),1);
        panel.innerHTML=`<div class="ml-kpis">${stat('NET REVENUE',money(data.totals.net_revenue),change('net_revenue'))}${stat('COMPLETED ORDERS',fmt(data.totals.completed_orders),change('completed_orders'))}${stat('AOV',money(data.totals.aov),change('aov'))}${stat('LOW STOCK',fmt(low.length),'Products requiring attention')}</div>
          <div class="ml-two-col ml-dashboard-grid"><section class="card"><h3>Revenue <em>Net revenue trend</em></h3>${chart(data.series.map(r=>({period:r.date,actual:r.net_revenue})),[],data.currency,true)}<div class="ml-card-foot"><span>${date(data.start)} – ${date(data.end)}</span><span>Currency: ${esc(data.currency)}</span></div></section>
          <section class="card"><h3>Action required <em>Action items</em></h3><div class="ml-action-item"><span><span class="b w">${data.pending_orders}</span> Orders awaiting processing</span><a class="btn gh sm" href="#/admin/orders">View</a></div><div class="ml-action-item"><span><span class="b w">${low.length}</span> Products running low</span><a class="btn gh sm" href="#/admin/inventory">View</a></div><div class="ml-action-item"><span>Review demand and restock suggestions</span><a class="btn sm" href="#/admin/forecast">Open forecast</a></div></section>
          <section class="card"><h3>Top selling products <em>By net revenue</em></h3>${data.top_products.slice(0,6).map(r=>`<div class="ml-category"><div><span>${esc(r.name)}</span><b>${money(r.net_revenue)}</b></div><div class="ml-meter"><span style="width:${r.net_revenue/max*100}%"></span></div></div>`).join('')||'<p class="muted">No completed sales in this period.</p>'}</section>
          <section class="card"><h3>Low stock alerts <em>Available stock</em></h3>${low.slice(0,6).map(r=>`<div class="ml-action-item"><span>${esc(r.name)}<br><small class="muted">${esc(r.sku_code)} · ${esc(r.size)}</small></span><span class="b w">${fmt(r.on_hand-r.reserved)} left</span></div>`).join('')||'<p class="muted">All products have sufficient stock.</p>'}<a class="btn gh sm" href="#/admin/inventory">View inventory</a></section></div>`;
      }catch(error){if(!disposed&&request===version)failure(panel,error,load);}
    }
  }

  async function mountForecast(root, helpers) {
    cleanup();let disposed=false,timer=null,version=0;
    cleanup=()=>{disposed=true;clearTimeout(timer);};
    root.innerHTML='<div class="view ml-view"><p role="status">Loading forecast…</p></div>';
    const qs = selector => root.querySelector(selector);
    let status, stockRows=[],stockPage=1;
    let frequency='weekly',target='revenue_gbp',category='';
    try {
      status=await api('ml/status');if(disposed)return;
      root.innerHTML=`<div class="view ml-view">${heading('Revenue forecast','', '<button type="button" aria-label="Toggle light and dark mode" title="Toggle light and dark mode" class="btn gh theme-toggle"><svg viewbox="0 0 24 24" aria-hidden="true"><path d="M20 13a8 8 0 0 1-9-9 8 8 0 1 0 9 9Z"></path></svg></button><button class="btn" data-ml="inference">Refresh forecast</button>')}
        <div class="ml-job" role="status" aria-live="polite" hidden></div>
        <div class="card ml-section"><div class="ml-controls"><label>Frequency<select aria-label="Forecast frequency"><option value="weekly">Weekly · 12 weeks</option><option value="monthly">Monthly · 3 months</option></select></label><label>Measure<select aria-label="Forecast measure"><option value="revenue_gbp">Revenue · GBP</option><option value="quantity_sold">Quantity · units</option></select></label><label>Category<select aria-label="Forecast category"><option value="">All categories</option>${Object.entries(status.categories).map(([key,name])=>`<option value="${esc(key)}">${esc(name)}</option>`).join('')}</select></label><a class="btn gh" data-export href="/api/ml/export">Export CSV</a></div><div class="ml-results" aria-live="polite"></div></div>
        <div class="card ml-section"><h3>Restock recommendations <em>Suggested quantities based on demand and available stock</em></h3><div class="ml-controls"><label>Search products<input type="search" aria-label="Search products" placeholder="Name or SKU"></label><label>Category<select aria-label="Restock category"><option value="">All categories</option>${Object.entries(status.categories).map(([key,name])=>`<option value="${esc(key)}">${esc(name)}</option>`).join('')}</select></label><label class="ml-checkbox"><input type="checkbox" aria-label="Needs action only" checked>Needs action only</label><span class="muted" data-stock-count></span></div><div class="tw ml-stock"></div><div class="ml-pager"></div></div>

</div>`;
      qs('[aria-label="Forecast frequency"]').onchange=e=>{frequency=e.target.value;updateForecast();};
      qs('[aria-label="Forecast measure"]').onchange=e=>{target=e.target.value;updateForecast();};
      qs('[aria-label="Forecast category"]').onchange=e=>{category=e.target.value;updateForecast();};
      qs('[aria-label="Search products"]').oninput=()=>{stockPage=1;drawStock();};
      qs('[aria-label="Restock category"]').onchange=()=>{stockPage=1;drawStock();};
      qs('[aria-label="Needs action only"]').onchange=()=>{stockPage=1;drawStock();};
      root.addEventListener('click',onClick);
      cleanup=()=>{disposed=true;clearTimeout(timer);root.removeEventListener('click',onClick);};
      await Promise.all([updateForecast(),updateStock().catch(error=>{if(!disposed)qs('.ml-stock').innerHTML=`<p class="local-message" role="alert">${esc(error.message)}</p>`;})]);
      updateJob();
      if(status.job?.status==='running')pollJob();
    } catch(error) {if(!disposed)failure(root,error,()=>mountForecast(root,helpers));}

    async function updateForecast() {
      const requestVersion=++version;
      const panel=qs('.ml-results');panel.innerHTML='<p role="status">Loading forecast…</p>';
      const query=new URLSearchParams({frequency,target,category});qs('[data-export]').href='/api/ml/export?'+query;
      try {
        const result=await api('ml/forecast?'+query);if(disposed||requestVersion!==version)return;
        const rows=result.series.filter(r=>r.role==='champion');
        panel.innerHTML=`<h3>${target==='revenue_gbp'?'Revenue · GBP':'Quantity · units'} <em>${esc(catName(category)||'All categories')}</em></h3>${chart(result.history,rows,result.currency)}<p class="local-note">Forecast period: ${date(rows[0]?.period)} – ${date(rows.at(-1)?.period)} · ${frequency==='weekly'?'Weekly':'Monthly'}</p><details><summary>View forecast values (${rows.length} periods)</summary><div class="tw"><table><thead><tr><th>Period starting</th><th>Forecast · ${esc(result.currency)}</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${date(r.period)}</td><td>${fmt(r.forecast,2)}</td></tr>`).join('')}</tbody></table></div></details>`;
      }catch(error){if(!disposed&&requestVersion===version)failure(panel,error,updateForecast);}
    }
    async function updateStock() {
      const result=await api('ml/recommendations');if(disposed)return;
      stockRows=result.rows;drawStock();
    }

    function drawStock() {
      const query=qs('[aria-label="Search products"]').value.toLowerCase(),cat=qs('[aria-label="Restock category"]').value,needs=qs('[aria-label="Needs action only"]').checked;
      const rows=stockRows.filter(r=>(!needs||r.suggested>0)&&(!cat||r.category===cat)&&(!query||(r.name+' '+r.sku).toLowerCase().includes(query)));
      const totalPages=Math.max(1,Math.ceil(rows.length/15));stockPage=Math.max(1,Math.min(stockPage,totalPages));
      qs('.ml-pager').innerHTML=`<span>${rows.length} products · page ${stockPage} / ${totalPages}</span><div><button class="btn gh sm" data-ml="prev" aria-label="Stock previous page" ${stockPage===1?'disabled':''}>Previous</button> <button class="btn gh sm" data-ml="next" aria-label="Stock next page" ${stockPage===totalPages?'disabled':''}>Next</button></div>`;
      qs('[data-stock-count]').textContent=`${rows.length} / ${stockRows.length} products`;
      qs('.ml-stock').innerHTML=`<table><thead><tr><th>SKU / Product</th><th>Forecast demand</th><th>Safety</th><th>Available</th><th>Incoming</th><th>Suggested</th><th>Actions</th></tr></thead><tbody>${rows.slice((stockPage-1)*15,stockPage*15).map(r=>`<tr><td class="wrap"><b>${esc(r.sku)}</b><br>${esc(r.name)}<br><small class="muted">${esc(r.size)} · ${esc(catName(r.category))}</small></td><td>${fmt(r.forecast,2)}</td><td>${r.safety_stock}</td><td>${r.available}</td><td>${r.incoming}</td><td><span class="b ${r.suggested?'w':'g'}">${fmt(r.suggested)}</span></td><td><div class="ml-row-actions"><button class="btn sm" data-ml="order" data-product="${r.product_id}">Order Stock</button><button class="btn sm gh" data-ml="receive" data-product="${r.product_id}" ${r.incoming?'':'disabled'}>Receive Stock</button></div></td></tr>`).join('')||'<tr><td colspan="7" class="local-empty">No matching products. Change the search or filters.</td></tr>'}</tbody></table>`;
    }
    function updateJob() {
      const job=status.job,busy=job?.status==='running';
      qs('.ml-job').hidden=!busy;
      qs('.ml-job').textContent=busy?'Updating forecast… You can continue managing your store.':'';
      qs('.ml-job').classList.toggle('ml-error',job?.status==='failed');
      root.querySelectorAll('[data-ml="train"],[data-ml="inference"]').forEach(b=>b.disabled=busy);
    }
    function pollJob() {
      clearTimeout(timer);timer=setTimeout(async()=>{
        if(disposed)return;
        try {
          status=await api('ml/status');if(disposed)return;updateJob();
          if(status.job?.status==='running')pollJob();
          else {helpers.toast(status.job?.status==='failed'?'Could not refresh forecast. Please try again.':'Forecast updated.');await Promise.all([updateForecast(),updateStock()]);}
        }catch(error){if(!disposed){qs('.ml-job').hidden=false;qs('.ml-job').textContent='Unable to check progress. Retrying…';timer=setTimeout(pollJob,3000);}}
      },2000);
    }
    function onClick(event) {
      const button=event.target.closest('[data-ml]');if(!button||button.disabled)return;
      event.stopPropagation();const action=button.dataset.ml;
      if(action==='prev'||action==='next'){stockPage+=action==='next'?1:-1;drawStock();return;}
      if(action==='inference') {
        helpers.openModal('Refresh forecast','<p>Update forecasts and stock suggestions using the latest available sales history. This may take a few moments.</p>','Refresh forecast',async()=>{
          const confirm=document.querySelector('[data-confirm]');confirm.disabled=true;
          try{const result=await api('ml/jobs',{action});helpers.closeModal();if(disposed)return;status.job=result.job;updateJob();pollJob();}
          catch(error){helpers.modalError(error.message);confirm.disabled=false;}
        });return;
      }
      const product=stockRows.find(r=>r.product_id===button.dataset.product);if(!product)return;
      helpers.openModal(action==='order'?'Place stock order':'Receive stock',`<p>${esc(product.name)}</p><label>Quantity<input id="ml-quantity" type="number" min="1" max="${action==='receive'?product.incoming:1000000}" step="1" value="${action==='receive'?product.incoming:(product.suggested||1)}"></label><small class="muted">${action==='order'?'Record incoming stock. Contact your supplier separately to place the order.':'Add received items to on-hand stock.'}</small>`,action==='order'?'Order Stock':'Receive Stock',async()=>{
        const input=document.querySelector('#ml-quantity');if(!input.reportValidity())return;
        const quantity=Number(input.value);if(!Number.isInteger(quantity)||quantity<1)return helpers.modalError('Enter a positive whole quantity.');
        const confirm=document.querySelector('[data-confirm]');confirm.disabled=true;
        try{await api('ml/stock',{product_id:product.product_id,action,quantity});helpers.closeModal();if(!disposed){await updateStock();helpers.toast('Stock updated.');}}
        catch(error){helpers.modalError(error.message);confirm.disabled=false;}
      });
    }
  }
  window.FOLMARE_ML={api, session:()=>api('session'),login:async(email,password)=>{await api('session');return api('login',{email,password});},logout:()=>api('logout',{}),dispose:()=>cleanup(),mountDashboard,mountForecast};
})();
