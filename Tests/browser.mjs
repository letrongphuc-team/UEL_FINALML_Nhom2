/* Reproducible UI checks against a SQL clone. The source DB business rows are not edited. */
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { existsSync,mkdirSync,writeFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { resolve } from 'node:path';
const output=resolve('Tests/artifacts');mkdirSync(output,{recursive:true});
const base='http://127.0.0.1:4189',checks=[],errors=[];
const check=(name,value=true)=>{assert.ok(value,name);checks.push(name);console.log('PASS '+name);};
let server,browser;
try{
  server=spawn(resolve('.venv/bin/python'),['-B','Tests/browser_server.py'],{env:process.env,stdio:'ignore'});
  let ready=false;
  for(let i=0;i<120;i++){try{if((await fetch(base+'/api/session')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,500));}
  assert.ok(ready,'SQL clone UI server did not start');
  const chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  browser=await chromium.launch({headless:true,...(existsSync(chrome)?{executablePath:chrome}:{})});
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base+'/#/admin/forecast');await page.locator('input[type=email]').waitFor();
  check('Unauthenticated forecast requires database sign in',page.url().includes('/admin/login'));
  await page.locator('input[type=email]').fill('admin@haircare.vn');await page.locator('input[type=password]').fill('admin123');await page.locator('form button').click();await page.getByRole('alert').waitFor();check('Old frontend-only account is rejected');
  await page.locator('input[type=email]').fill('manager@haircare.local');await page.locator('input[type=password]').fill('DemoAdmin123!');await page.locator('form button').click();
  await page.locator('.ml-stock tbody tr').first().waitFor({timeout:60000});
  const status=await (await context.request.get(base+'/api/ml/status')).json();
  check('Using an isolated real MySQL database',status.database.name.startsWith('folmare_ui_')&&status.source==='mysql:Daily_Sales');
  check('Admin UI hides connection and evaluation details',!(/MySQL|SHA-256|WAPE|before \/ after|Model comparison/i.test(await page.locator('body').innerText())));
  check('Weekly forecast has 12 periods',await page.locator('.ml-results details tbody tr').count()===12);
  check('Stock pagination uses 15 records',await page.locator('.ml-stock tbody tr').count()===15);
  await page.getByRole('button',{name:'Stock next page'}).click();check('Stock next page works',(await page.locator('.ml-pager').innerText()).includes('page 2'));
  await page.getByRole('button',{name:'Stock previous page'}).click();
  for(const category of Object.keys(status.categories)){
    const response=page.waitForResponse(r=>r.url().includes('/api/ml/forecast?')&&r.status()===200);
    await page.getByLabel('Forecast category',{exact:true}).selectOption(category);await response;await page.locator('.ml-results details').waitFor();
    check('Forecast category '+category,await page.locator('.ml-results details tbody tr').count()===12);
  }
  let response=page.waitForResponse(r=>r.url().includes('/api/ml/forecast?')&&r.status()===200);
  await page.getByLabel('Forecast frequency').selectOption('monthly');await response;await page.locator('.ml-results details').waitFor();check('Monthly forecast has 3 periods',await page.locator('.ml-results details tbody tr').count()===3);
  response=page.waitForResponse(r=>r.url().includes('/api/ml/forecast?')&&r.status()===200);
  await page.getByLabel('Forecast measure').selectOption('quantity_sold');await response;await page.locator('.ml-results details').waitFor();check('Quantity target uses units',(await page.locator('.ml-results').innerText()).includes('Quantity · units'));
  const download=page.waitForEvent('download');await page.getByRole('link',{name:'Export CSV',exact:true}).click();check('SQL forecast CSV download',(await download).suggestedFilename()==='folmare-forecast.csv');
  for(const resource of [{name:'Products',heading:'Products',key:'products'},{name:'Inventory',heading:'Inventory',key:'products'},{name:'Orders',heading:'Orders',key:'orders'},{name:'Customers',heading:'Customers',key:'customers'},{name:'Reviews',heading:'Reviews',key:'reviews'},{name:'Discount codes',heading:'Discount codes',key:'vouchers'}]){
    const actual=await (await context.request.get(base+'/api/admin/'+resource.key)).json();
    await page.getByRole('link',{name:resource.name,exact:true}).click();await page.getByRole('heading',{name:resource.heading,exact:true}).waitFor();await page.locator('[data-content] table').waitFor();
    check(resource.name+' matches SQL count',(await page.locator('[data-content]').innerText()).includes(actual[resource.key].length+' records'));
  }
  await page.getByRole('link',{name:'Products',exact:true}).click();await page.getByRole('heading',{name:'Products',exact:true}).waitFor();await page.locator('[data-content] table').waitFor();
  await page.getByLabel('Search records').fill('no-such-product');check('Product search empty state',(await page.locator('[data-content]').innerText()).includes('No matching records.'));await page.getByLabel('Search records').fill('');
  await page.getByRole('link',{name:'Edit',exact:true}).first().click();await page.locator('[data-product-form]').waitFor();
  const id=page.url().split('/products/')[1].split('/')[0],price=Number(await page.locator('input[name=price]').inputValue());
  await page.locator('input[name=price]').fill(String(price+1));await page.getByRole('button',{name:'Save product',exact:true}).click();await page.locator('[data-content] table').waitFor();
  const products=(await (await context.request.get(base+'/api/admin/products')).json()).products;
  check('Product edit persisted to MySQL',Number(products.find(p=>p.product_id===id).price)===price+1);
  await page.getByRole('link',{name:'Inventory',exact:true}).click();await page.getByRole('heading',{name:'Inventory',exact:true}).waitFor();await page.locator('[data-content] table').waitFor();
  const row=page.locator('[data-content] tbody tr').first(),before=await row.locator('td').allInnerTexts();
  await row.getByRole('button',{name:'Order Stock',exact:true}).click();await page.locator('#admin-stock-quantity').fill('0');await page.locator('#admin-stock-reason').fill('Isolated UI test');await page.locator('[data-confirm]').click();check('Inventory rejects zero quantity',await page.getByRole('dialog').count()===1);
  await page.locator('#admin-stock-quantity').fill('3');await page.locator('[data-confirm]').click();await page.getByRole('dialog').waitFor({state:'hidden'});
  await row.getByRole('button',{name:'Receive Stock',exact:true}).click();check('Incoming stock updated in database',Number(await page.locator('#admin-stock-quantity').inputValue())===Number(before[4])+3);
  await page.locator('#admin-stock-quantity').fill('1');await page.locator('#admin-stock-reason').fill('Isolated UI receipt');await page.locator('[data-confirm]').click();await page.getByRole('dialog').waitFor({state:'hidden'});await page.reload();await page.locator('[data-content] table').waitFor();
  check('Stock receipt survives reload',Number(await row.locator('td').nth(4).innerText())===Number(before[4])+2);
  await page.getByRole('link',{name:'Settings',exact:true}).click();await page.locator('[data-settings-form]').waitFor();await page.locator('input[name=safety_stock]').fill('16');await page.getByRole('button',{name:'Save changes',exact:true}).click();await page.getByText('Settings saved.',{exact:true}).waitFor();
  check('Settings saved to MySQL',Number((await (await context.request.get(base+'/api/admin/settings')).json()).settings.safety_stock)===16);
  await page.getByRole('link',{name:'Revenue forecast',exact:true}).click();await page.locator('.ml-stock tbody tr').first().waitFor({timeout:30000});check('Forecast reads the same SQL Settings',await page.locator('.ml-stock tbody tr td:nth-child(3)').allInnerTexts().then(c=>c.every(v=>v==='16')));
  await page.screenshot({path:resolve(output,'mysql-forecast-desktop.png'),fullPage:false});
  for(const viewport of [{width:390,height:844},{width:768,height:1024},{width:844,height:390}]){await page.setViewportSize(viewport);check('No outer overflow '+viewport.width,await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));}
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:resolve(output,'mysql-forecast-mobile.png'),fullPage:false});
  await page.setViewportSize({width:1440,height:1000});await page.getByRole('button',{name:'Log out',exact:true}).click();await page.locator('input[type=email]').waitFor();check('Logout ends the server session',(await context.request.get(base+'/api/ml/status')).status()===401);
  check('No browser JavaScript errors',errors.length===0);
  writeFileSync(resolve(output,'mysql-browser-results.json'),JSON.stringify({passed:checks.length,checks,errors},null,2));
}finally{
  await browser?.close();
  if(server){const exit=new Promise(resolve=>server.once('exit',resolve));server.kill('SIGTERM');await Promise.race([exit,new Promise(resolve=>setTimeout(resolve,10000))]);}
}
