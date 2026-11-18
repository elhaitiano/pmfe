(() => {
 'use strict';
 const button=document.getElementById('toggle-password'),password=document.getElementById('txtSenha');
 button.addEventListener('click',()=>{
  const visible=password.type==='password';password.type=visible?'text':'password';
  button.setAttribute('aria-pressed',String(visible));button.setAttribute('aria-label',visible?'Ocultar palavra-passe':'Mostrar palavra-passe');
  document.getElementById('eyeIcon').textContent=visible?'visibility_off':'visibility';
 });
 document.getElementById('forgot-password').addEventListener('click',()=>{document.getElementById('msgStatus').textContent='Contacte o administrador local para repor a palavra-passe.';});
})();

// Keep the existing native Django POST, after the visible water-fill sequence.
(() => {
 const form=document.getElementById('loginForm'),button=document.getElementById('btnEntrar');
 const overlay=document.getElementById('epalEntry');let timer=null,busy=false;
 function reset(){clearTimeout(timer);busy=false;button.disabled=false;form.removeAttribute('aria-busy');document.documentElement.classList.remove('epal-entering');window.pmfEntryWater?.stop();}
 window.addEventListener('pageshow',reset);
 form.addEventListener('submit',event=>{
  if(!form.checkValidity())return;
  event.preventDefault();if(busy)return;busy=true;button.disabled=true;form.setAttribute('aria-busy','true');
  document.documentElement.classList.add('epal-entering');window.pmfEntryWater?.start(Date.now());
  timer=setTimeout(()=>{HTMLFormElement.prototype.submit.call(form);},3300);
 });
})();
