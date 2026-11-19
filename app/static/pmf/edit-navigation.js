(()=>{'use strict';
  const config=document.getElementById('pmfe-edit-navigation');if(!config)return;
  let flow;try{flow=JSON.parse(config.textContent);}catch{return;}
  const local=raw=>{try{const u=new URL(raw,location.href);return u.origin===location.origin&&!/[\\\r\n]/.test(raw)?u:null;}catch{return null;}};
  const root=local(flow.origin);if(!root)return;
  const destination=root.pathname+root.search+root.hash;
  const key='pmfe-edit-scroll:'+root.pathname+root.search;
  const tracked=/^\/(?:funcionarios\/\d+\/(?:dados\/|editar\/)?|formacoes\/\d+\/editar\/|avaliacao\/(?:\d+\/(?:formacao\/|reaccao\/)?)?|validacao\/[^/]+\/\d+\/|planeamento\/validacao\/)$/;
  function remember(){try{sessionStorage.setItem(key,JSON.stringify({x:scrollX,y:scrollY,time:Date.now()}));}catch{}}
  function enhance(a){
    if(a.closest('#mainSidebar')||a.hasAttribute('download'))return;
    const u=local(a.getAttribute('href'));if(!u||!tracked.test(u.pathname))return;
    if(!u.searchParams.has('return_to')){u.searchParams.set('return_to',destination);a.href=u.pathname+u.search+u.hash;}
  }
  document.querySelectorAll('a[href]').forEach(enhance);
  document.addEventListener('click',event=>{const a=event.target.closest('a[href]');if(!a)return;enhance(a);const u=local(a.href);if(u&&tracked.test(u.pathname)&&location.pathname===root.pathname&&location.search===root.search)remember();},true);
  if(location.pathname===root.pathname&&location.search===root.search){
    try{const raw=sessionStorage.getItem(key);if(raw){const state=JSON.parse(raw);sessionStorage.removeItem(key);if(Date.now()-state.time<3600000){const restore=()=>scrollTo(state.x,state.y);if(document.readyState==='complete')restore();else addEventListener('load',restore,{once:true});setTimeout(restore,150);}}}catch{}
  }
})();
