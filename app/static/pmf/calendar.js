/* Offline date picker. Text remains DD/MM/YYYY; native picker submits no field. */
(() => {
  const iso = value => {const m=/^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value);return m?`${m[3]}-${m[2]}-${m[1]}`:value;};
  function enhance(root=document) {
    root.querySelectorAll('input[data-pmf-calendar], input[placeholder="dd/mm/aaaa"]').forEach(input=>{
      if(input.dataset.calendarReady || input.disabled || input.readOnly)return;
      input.dataset.calendarReady='1';
      const wrap=document.createElement('span');wrap.className='pmfe-date-wrap';input.before(wrap);wrap.append(input);
      const picker=document.createElement('input');picker.type='date';picker.lang='pt-PT';picker.className='pmfe-date-native';picker.tabIndex=-1;picker.setAttribute('aria-label','Calendário: '+(input.labels?.[0]?.textContent||'data'));wrap.append(picker);
      const button=document.createElement('button');button.type='button';button.className='pmfe-date-button';button.textContent='▦';button.setAttribute('aria-label','Abrir calendário: '+(input.labels?.[0]?.textContent||'data'));wrap.append(button);
      function open(){picker.value=iso(input.value);if(input.min)picker.min=iso(input.min);if(input.max)picker.max=iso(input.max);try{picker.showPicker();}catch(_){picker.focus();picker.click();}}
      button.addEventListener('click',open);input.addEventListener('click',open);
      input.addEventListener('keydown',e=>{if(e.altKey&&e.key==='ArrowDown'){e.preventDefault();open();}});
      picker.addEventListener('change',()=>{const m=/^(\d{4})-(\d{2})-(\d{2})$/.exec(picker.value);input.value=m?`${m[3]}/${m[2]}/${m[1]}`:'';input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));input.focus();});
    });
    root.querySelectorAll('input[type="date"]').forEach(input=>{if(input.classList.contains('pmfe-date-native')||input.dataset.calendarReady)return;input.dataset.calendarReady='1';input.lang='pt-PT';input.addEventListener('click',()=>{try{input.showPicker();}catch(_){}});});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>enhance());else enhance();
  const observer=new MutationObserver(entries=>{if(entries.some(e=>e.addedNodes.length))enhance();});observer.observe(document.documentElement,{subtree:true,childList:true});
})();
