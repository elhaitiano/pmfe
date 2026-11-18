(() => {
 const field=document.querySelector('[name="session_dates"]');if(!field)return;
 const first=document.querySelector('[name="start"]'),last=document.querySelector('[name="end"]'),output=document.querySelector('#planDuration');
 const grid=document.createElement('fieldset'),legend=document.createElement('legend');legend.textContent='Seleccione os dias efectivos (nenhum dia automatico)';grid.append(legend);grid.style.gridColumn='1 / -1';field.closest('label').after(grid);
 const parse=value=>{if(/^\d{4}-\d{2}-\d{2}$/.test(value))return value;const m=/^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value);return m?m[3]+'-'+m[2]+'-'+m[1]:'';};
 const selected=()=>new Set(field.value.trim().split(/[\s,;]+/).filter(Boolean));
 function count(){if(output)output.textContent=selected().size+' dia(s) seleccionado(s)';}
 function draw(){
  grid.querySelectorAll('label,p').forEach(x=>x.remove());const a=parse(first.value),b=parse(last.value),chosen=selected();if(!a||!b){count();return;}
  const start=new Date(a+'T12:00:00Z'),end=new Date(b+'T12:00:00Z');if(!Number.isFinite(+start)||!Number.isFinite(+end)||end<start||(end-start)/86400000>365){count();return;}
  for(let d=new Date(start);d<=end;d.setUTCDate(d.getUTCDate()+1)){
   const iso=d.toISOString().slice(0,10),label=document.createElement('label'),box=document.createElement('input');
   label.style.cssText='display:inline-flex;gap:6px;margin:8px';box.type='checkbox';box.value=iso;box.checked=chosen.has(iso);label.append(box,document.createTextNode(iso));grid.append(label);
   box.addEventListener('change',()=>{const values=selected();if(box.checked)values.add(iso);else values.delete(iso);field.value=[...values].sort().join('\n');count();});
  }count();
 }
 [first,last].forEach(x=>x.addEventListener('change',draw));field.addEventListener('input',draw);draw();
})();
