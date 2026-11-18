(() => {
 'use strict';const root=document.querySelector('[data-coverage-workspace]');if(!root)return;
 const status=root.querySelector('#coverage-status'),nominal=root.querySelector('#coverage-nominal');let controller,serial=0;
 function open(id){const d=document.getElementById(id);if(d&&!d.open)d.showModal();}
 root.querySelectorAll('[data-coverage-open]').forEach(b=>b.addEventListener('click',()=>open(b.dataset.coverageOpen)));
 root.querySelectorAll('[data-coverage-close]').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));
 async function load(a){
  const url=new URL(a.href,location.href);if(url.origin!==location.origin)return;controller?.abort();controller=new AbortController();const current=++serial;status.textContent='A consultar a lista nominal…';
  const button=a;button.setAttribute('aria-busy','true');
  try{const response=await fetch(url,{credentials:'same-origin',signal:controller.signal,headers:{Accept:'text/html'}});if(!response.ok)throw Error('Consulta indisponível.');const doc=new DOMParser().parseFromString(await response.text(),'text/html'),body=doc.querySelector('#coverage-nominal-body');if(!body||current!==serial)throw Error('Não foi possível abrir a consulta.');root.querySelector('#coverage-nominal-body').replaceChildren(...[...body.childNodes].map(n=>document.importNode(n,true)));open('coverage-nominal');nominal.querySelector('.coverage-dialog-content').scrollTop=0;status.textContent='Lista nominal carregada.';}catch(e){if(e.name!=='AbortError'){status.textContent='Não foi possível abrir a lista. Tente novamente ou abra a ligação numa nova página.';}}finally{button.removeAttribute('aria-busy');}
 }
 // Capture before the generic workspace handler, so nominal queries stay in this page.
 document.addEventListener('click',e=>{const a=e.target.closest('.coverage-drill');if(!a||!root.contains(a)||e.button!==0||e.ctrlKey||e.metaKey||e.shiftKey||e.altKey)return;e.preventDefault();e.stopImmediatePropagation();load(a);},true);
 root.addEventListener('click',e=>{const a=e.target.closest('a[href]');if(a&&!a.classList.contains('coverage-drill')&&new URL(a.href,location.href).pathname===location.pathname)e.stopPropagation();},true);
 root.addEventListener('click',e=>{const summary=e.target.closest('#coverage-nominal summary');if(!summary)return;e.preventDefault();const target=root.querySelector('#coverage-editions-body'),details=summary.parentElement;target.replaceChildren();const name=document.createElement('h3');name.textContent=summary.closest('tr')?.querySelector('td:nth-child(2)')?.textContent||'Participação';target.append(name);[...details.childNodes].filter(n=>n!==summary).forEach(n=>target.append(n.cloneNode(true)));open('coverage-editions');});
 root.querySelector('#id_q')?.setAttribute('data-pmfe-person-search','true');
 if(root.querySelector('.errorlist'))open('coverage-more-filters');else if(nominal.hasAttribute('data-open-on-load'))open('coverage-nominal');
 root.querySelectorAll('dialog').forEach(d=>d.addEventListener('keydown',e=>{if(e.key==='Escape')e.stopPropagation();}));
})();
