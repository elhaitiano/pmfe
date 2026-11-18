(function(){
'use strict';
// Sign-in only: no minimum wait and no splash on internal navigation.
const root=document.documentElement,overlay=document.getElementById('epalEntry');
if(!overlay||overlay.dataset.page!=='login')return;
function reset(){
 if(window.pmfEntryWater)window.pmfEntryWater.stop();
 root.classList.remove('epal-entering');overlay.style.opacity='';
 try{sessionStorage.removeItem('pmf.entry.started');}catch(e){}
 const button=document.getElementById('btnEntrar');if(button)button.disabled=false;
}
reset();window.addEventListener('pageshow',reset);
const form=document.getElementById('loginForm');
if(form)form.addEventListener('submit',function(){
 if(!form.checkValidity())return;
 root.classList.add('epal-entering');
 if(window.pmfEntryWater)window.pmfEntryWater.start(Date.now());
 const button=document.getElementById('btnEntrar');if(button)button.disabled=true;
});
})();
