(function(){'use strict';
  const startDate = document.getElementById('id_start');
  const endDate = document.getElementById('id_end');
  const duration = document.getElementById('planDuration');
  if (startDate && endDate && duration) {
    const parseDate = value => {
      const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value.trim());
      if (!match) return NaN;
      const [, day, month, year] = match.map(Number);
      const parsed = new Date(0);
      parsed.setUTCFullYear(year, month - 1, day);
      parsed.setUTCHours(0, 0, 0, 0);
      return year >= 1 && parsed.getUTCFullYear() === year &&
        parsed.getUTCMonth() === month - 1 && parsed.getUTCDate() === day
        ? parsed.getTime() : NaN;
    };
    const updateDuration = () => {
      if (!startDate.value || !endDate.value) {
        duration.textContent = '—';
        return;
      }
      const first = parseDate(startDate.value);
      const last = parseDate(endDate.value);
      const days = Math.round((last - first) / 86400000) + 1;
      duration.textContent = !Number.isFinite(days) || days < 1
        ? 'Verifique as datas'
        : days > 366 ? 'Máximo de 366 dias' : days + (days === 1 ? ' dia' : ' dias');
    };
    startDate.addEventListener('input', updateDuration);
    endDate.addEventListener('input', updateDuration);
    updateDuration();
  }
  const form=document.getElementById('planSelection');if(!form)return;const key='pmf.plan.selection.'+form.dataset.scope;let selected=new Set();try{selected=new Set(JSON.parse(sessionStorage.getItem(key)||'[]').filter(x=>/^\d+$/.test(x)));}catch(e){}const boxes=[...form.querySelectorAll('[data-person]')];boxes.forEach(b=>{if(b.disabled)selected.delete(b.value);else b.checked=selected.has(b.value);});function sync(){boxes.forEach(b=>{if(b.checked&&!b.disabled)selected.add(b.value);else selected.delete(b.value);});try{sessionStorage.setItem(key,JSON.stringify([...selected]));}catch(e){}document.getElementById('planSelectedCount').textContent=selected.size+' seleccionados';const all=document.getElementById('planSelectPage');const available=boxes.filter(b=>!b.disabled);all.checked=available.length>0&&available.every(b=>b.checked);all.indeterminate=available.some(b=>b.checked)&&!all.checked;}boxes.forEach(b=>b.addEventListener('change',sync));document.getElementById('planSelectPage').addEventListener('change',function(){boxes.filter(b=>!b.disabled).forEach(b=>b.checked=this.checked);sync();});document.getElementById('planClearSelection').addEventListener('click',()=>{selected.clear();boxes.forEach(b=>b.checked=false);sync();});form.addEventListener('submit',e=>{sync();if(!selected.size){e.preventDefault();document.getElementById('planSelectedCount').textContent='Seleccione pelo menos um formando.';return;}const carry=document.getElementById('planCarriedSelection');carry.replaceChildren();const shown=new Set(boxes.map(b=>b.value));selected.forEach(id=>{if(!shown.has(id)){const input=document.createElement('input');input.type='hidden';input.name='employees';input.value=id;carry.append(input);}});});sync();})();
