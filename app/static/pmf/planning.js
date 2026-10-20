// Presentation only: the server independently validates and calculates duration.
(function () {
  const form = document.getElementById('planDurationForm');
  if (!form) return;
  const days = form.querySelector('#id_default_days');
  const dates = form.querySelector('#id_dates');
  const hours = form.querySelector('#id_default_hours, #id_hours_per_day');
  const output = document.getElementById('planTotalHours');
  function update() {
    const entries = dates ? dates.value.replace(/,/g, '\n').split(/\r?\n/).map(x => x.trim()).filter(Boolean) : [];
    const validDates = entries.every(x => {const d = new Date(x + 'T00:00:00Z'); return /^\d{4}-\d{2}-\d{2}$/.test(x) && !isNaN(d) && d.toISOString().slice(0,10) === x;});
    const count = dates ? (validDates && new Set(entries).size === entries.length ? entries.length : NaN) : Number(days.value);
    const raw = hours.value.trim().replace(',', '.');
    const hundredths = Math.round(Number(raw) * 100);
    const valid = raw && /^\d+(?:\.\d{1,2})?$/.test(raw) && hundredths >= 50 && hundredths <= 2400 && Number.isInteger(count) && count >= 1 && count <= 366;
    output.textContent = valid ? (count * hundredths / 100).toLocaleString('pt-PT', {maximumFractionDigits:2}) + ' H' : 'Por confirmar';
  }
  [days, dates, hours].filter(Boolean).forEach(field => field.addEventListener('input', update));
  document.getElementById('planBuildCalendar')?.addEventListener('click', () => setTimeout(update, 0));
  update();
})();
(function(){'use strict';const build=document.getElementById('planBuildCalendar');if(build)build.addEventListener('click',()=>{const day=document.getElementById('planFirstDay').value;const count=Number(document.getElementById('planDayCount').value);const message=document.getElementById('planCalendarMessage');if(!day||!Number.isInteger(count)||count<1||count>366){message.textContent='Indique o primeiro dia e entre 1 e 366 dias.';return;}const current=new Date(day+'T12:00:00Z');const dates=[];const weekdays=document.getElementById('planWeekdaysOnly').checked;while(dates.length<count){if(!weekdays||![0,6].includes(current.getUTCDay()))dates.push(current.toISOString().slice(0,10));current.setUTCDate(current.getUTCDate()+1);}document.getElementById('id_dates').value=dates.join('\n');message.textContent=count+' dias preenchidos. Reveja feriados e ajuste as datas antes de guardar.';});const form=document.getElementById('planSelection');if(!form)return;const key='pmf.plan.selection.'+form.dataset.scope;let selected=new Set();try{selected=new Set(JSON.parse(sessionStorage.getItem(key)||'[]').filter(x=>/^\d+$/.test(x)));}catch(e){}const boxes=[...form.querySelectorAll('[data-person]')];boxes.forEach(b=>{if(b.disabled)selected.delete(b.value);else b.checked=selected.has(b.value);});function sync(){boxes.forEach(b=>{if(b.checked&&!b.disabled)selected.add(b.value);else selected.delete(b.value);});try{sessionStorage.setItem(key,JSON.stringify([...selected]));}catch(e){}document.getElementById('planSelectedCount').textContent=selected.size+' seleccionados';const all=document.getElementById('planSelectPage');const available=boxes.filter(b=>!b.disabled);all.checked=available.length>0&&available.every(b=>b.checked);all.indeterminate=available.some(b=>b.checked)&&!all.checked;}boxes.forEach(b=>b.addEventListener('change',sync));document.getElementById('planSelectPage').addEventListener('change',function(){boxes.filter(b=>!b.disabled).forEach(b=>b.checked=this.checked);sync();});document.getElementById('planClearSelection').addEventListener('click',()=>{selected.clear();boxes.forEach(b=>b.checked=false);sync();});form.addEventListener('submit',e=>{sync();if(!selected.size){e.preventDefault();document.getElementById('planSelectedCount').textContent='Seleccione pelo menos um formando.';return;}const carry=document.getElementById('planCarriedSelection');carry.replaceChildren();const shown=new Set(boxes.map(b=>b.value));selected.forEach(id=>{if(!shown.has(id)){const input=document.createElement('input');input.type='hidden';input.name='employees';input.value=id;carry.append(input);}});});sync();})();
