(function(){
'use strict';
const key='pmf.entry.started',root=document.documentElement,overlay=document.getElementById('epalEntry');
if(!overlay)return;
let finishTimer;
function removeFlag(){try{sessionStorage.removeItem(key);}catch(e){}}
function start(time){if(window.pmfEntryWater)window.pmfEntryWater.start(time);}
function reset(){clearTimeout(finishTimer);if(window.pmfEntryWater)window.pmfEntryWater.stop();root.classList.remove('epal-entering');overlay.style.opacity='';const button=document.getElementById('btnEntrar');if(button)button.disabled=false;}
if(overlay.dataset.page==='login'){
 removeFlag();window.addEventListener('pageshow',function(){reset();removeFlag();});
 const form=document.getElementById('loginForm');
 if(form)form.addEventListener('submit',function(){if(!form.checkValidity())return;const time=Date.now();try{sessionStorage.setItem(key,String(time));}catch(e){}root.classList.add('epal-entering');start(time);const button=document.getElementById('btnEntrar');if(button)button.disabled=true;});
}else if(root.classList.contains('epal-entering')){
 let started=Date.now();try{started=Number(sessionStorage.getItem(key))||started;}catch(e){}removeFlag();
 const elapsed=Math.max(0,Date.now()-started);start(started);
 overlay.querySelector('.epal-entry-label').textContent='A preparar a plataforma…';
 const reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
 finishTimer=setTimeout(function(){overlay.style.opacity='0';finishTimer=setTimeout(reset,reduced?0:250);},reduced?100:Math.max(300,4800-elapsed));
 window.addEventListener('pageshow',function(event){if(event.persisted)reset();});
}
})();