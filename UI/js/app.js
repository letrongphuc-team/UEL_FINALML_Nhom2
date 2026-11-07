/* FOLMARÉ admin navigation and authenticated sessions. */
(() => {
  'use strict';
  const app=document.getElementById('app');
  const $=(s,root=document)=>root.querySelector(s);
  const $$=(s,root=document)=>[...root.querySelectorAll(s)];
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let user=null,theme='light',modalAction=null,lastFocus=null;
  try{theme=localStorage.getItem('folmare-theme')||'light';}catch{}
  function go(route){if(location.hash.slice(1)===route)render();else location.hash=route;}
  function toast(message){$('.local-toast')?.remove();const element=document.createElement('div');element.className='local-toast';element.setAttribute('role','status');element.textContent=message;$('.adm')?.append(element);setTimeout(()=>element.remove(),4500);}
  function openModal(title,content,label,action){
    lastFocus=document.activeElement;modalAction=action;
    $('#modal-root').innerHTML=`<div class="ov"><div role="dialog" aria-modal="true" class="md" aria-label="${esc(title)}"><h3>${esc(title)}</h3>${content}<div class="act"><button class="btn gh" data-close>Close</button><button class="btn" data-confirm>${esc(label)}</button></div></div></div>`;
    document.body.style.overflow='hidden';$('input,select,textarea,button',$('#modal-root'))?.focus();
  }
  function closeModal(){$('#modal-root').innerHTML='';document.body.style.overflow='';modalAction=null;if(lastFocus?.isConnected)lastFocus.focus();}
  function modalError(message){const root=$('#modal-root');if(!root?.firstChild)return toast(message);let element=$('.local-message',root);if(!element){element=document.createElement('p');element.className='local-message';element.setAttribute('role','alert');$('.act',root).before(element);}element.textContent=message;}
  const helpers={openModal,closeModal,modalError,toast,go};
  function render(){
    window.FOLMARE_ML.dispose();window.FOLMARE_ADMIN.dispose();document.body.style.overflow='';modalAction=null;
    const url=new URL(location.hash.slice(1)||'/admin/dashboard','http://local');const parts=url.pathname.split('/').filter(Boolean);
    const kind=parts[1]||'dashboard',id=parts[2];const login=kind==='login';
    if(!login&&!user){go('/admin/login?returnUrl='+encodeURIComponent(url.pathname+url.search));return;}
    document.title=(login?'Admin sign-in':kind==='dashboard'?'Overview':kind==='forecast'?'Revenue forecast':kind[0].toUpperCase()+kind.slice(1))+' | FOLMARÉ';
    app.innerHTML=`<div class="adm" data-theme="${theme}"><div class="app${login?' login':''}">${login?'':window.FOLMARE_SHELL}<main id="main-content" tabindex="-1">${login?window.FOLMARE_VIEWS.login:''}</main></div><div id="modal-root"></div></div>`;
    $$('[src^="/assets/"]').forEach(e=>e.setAttribute('src',e.getAttribute('src').slice(1)));
    $$('a[href^="/admin"]').forEach(e=>e.setAttribute('href','#'+e.getAttribute('href')));
    $$('nav .n').forEach(e=>e.remove());
    if(login){
      const form=$('form');form.setAttribute('novalidate','');$('input[type=email]').value='';$('input[type=password]').value='';
      form.addEventListener('submit',async event=>{
        event.preventDefault();const button=$('form button');button.disabled=true;
        try{const session=await window.FOLMARE_ML.login($('input[type=email]').value.trim(),$('input[type=password]').value);user=session.user;const next=url.searchParams.get('returnUrl');go(next?.startsWith('/admin/')?next:'/admin/dashboard');}
        catch(error){let alert=$('[role=alert]');if(!alert){alert=document.createElement('p');alert.className='local-message';alert.setAttribute('role','alert');button.before(alert);}alert.textContent=error.message;button.disabled=false;}
      });return;
    }
    const name=$('.admin-profile b');if(name)name.textContent=user.name||user.email;
    $$('nav a').forEach(a=>{const active=a.hash.startsWith('/admin/'+kind);a.classList.toggle('on',active);if(active)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
    const main=$('main');
    if(kind==='dashboard')window.FOLMARE_ML.mountDashboard(main);
    else if(kind==='forecast')window.FOLMARE_ML.mountForecast(main,helpers);
    else window.FOLMARE_ADMIN.mount(kind,id,main,helpers);
    window.scrollTo({top:0,behavior:'instant'});
  }
  document.addEventListener('click',async event=>{
    const button=event.target.closest('button');
    if(event.target.closest('.skip-link')){event.preventDefault();$('#main-content')?.focus();return;}
    if(!button||button.disabled)return;
    if(button.hasAttribute('data-close'))return closeModal();
    if(button.hasAttribute('data-confirm'))return modalAction?.();
    if(button.classList.contains('theme-toggle')){theme=theme==='light'?'dark':'light';$('.adm').dataset.theme=theme;try{localStorage.setItem('folmare-theme',theme);}catch{}return;}
    if(button.classList.contains('menu-btn')){const open=button.getAttribute('aria-expanded')!=='true';button.setAttribute('aria-expanded',open);$('#admin-nav').classList.toggle('open',open);return;}
    if(button.textContent.trim()==='Log out'){button.disabled=true;try{await window.FOLMARE_ML.logout();user=null;go('/admin/login');}catch(error){button.disabled=false;toast(error.message);}}
  });
  document.addEventListener('keydown',event=>{
    const modal=$('[role=dialog]');if(!modal)return;
    if(event.key==='Escape'){event.preventDefault();closeModal();}
    if(event.key==='Tab'){const controls=$$('button:not(:disabled),input,select,textarea,a[href]',modal);const first=controls[0],last=controls.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}
  });
  document.addEventListener('error',event=>{if(event.target.tagName==='IMG'&&!event.target.src.endsWith('placeholder.svg'))event.target.src='assets/placeholder.svg';},true);
  window.addEventListener('hashchange',render);
  window.FOLMARE_ML.session().then(session=>{user=session.user||null;render();}).catch(()=>{user=null;render();});
})();
