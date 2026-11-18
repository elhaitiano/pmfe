(() => {
  'use strict';
  const cardSelector='.exec-kpi-card,.exec-glass-box,.plan-course,.glass-panel,.gf-panel,.epal-quick-card,.epal-more-analysis,.coverage-page';
  document.querySelectorAll(cardSelector).forEach(card=>card.classList.add('epal-card'));
  document.querySelectorAll('.epal-kpis .exec-kpi-sub').forEach(note=>{note.title=note.textContent.trim();});
  const filters=document.querySelector('.epal-reference-filters');
  if(filters){const period=filters.elements.period,more=filters.querySelector('.epal-filter-more');const reveal=()=>{if(more&&['month','quarter','semester','custom'].includes(period?.value))more.open=true;};period?.addEventListener('change',reveal);reveal();}
  const palette=['#005bc0','#2e6eae','#41e4c0','#b0d2e4'];
  document.querySelectorAll('.epal-distribution-card .exec-donut a').forEach((link,i)=>{const circle=link.querySelector('circle');if(circle)circle.style.stroke=palette[i%palette.length];});
  document.querySelectorAll('.epal-distribution-card .exec-area-list .exec-bar-row').forEach((link,i)=>link.style.setProperty('--epal-segment',palette[i%palette.length]));
  const host=document.querySelector('.epal-evolution-card .exec-evolution');
  if(!host)return;
  const points=[...host.querySelectorAll('.exec-column')].map(el=>({value:Number(el.dataset.chartValue),label:el.dataset.chartLabel,href:el.getAttribute('href'),tip:el.dataset.tip}));
  if(!points.length||points.some(p=>!Number.isFinite(p.value)))return;
  const ns='http://www.w3.org/2000/svg';
  function node(name,attrs={},text){const el=document.createElementNS(ns,name);Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,String(v)));if(text!==undefined)el.textContent=text;return el;}
  const w=600,h=290,left=42,right=14,top=26,bottom=40,plotW=w-left-right,plotH=h-top-bottom,peak=Math.max(...points.map(p=>p.value),1);
  const svg=node('svg',{viewBox:`0 0 ${w} ${h}`,role:'img','aria-label':'Evolução das horas de formação, nos mesmos períodos e valores do indicador'});
  const defs=node('defs'),gradient=node('linearGradient',{id:'epal-line-gradient',x1:'0%',y1:'0%',x2:'100%',y2:'0%'});
  gradient.append(node('stop',{offset:'0%','stop-color':'#41e4c0'}),node('stop',{offset:'65%','stop-color':'#2e6eae'}),node('stop',{offset:'100%','stop-color':'#002b5c'}));
  const area=node('linearGradient',{id:'epal-area-gradient',x1:'0%',y1:'0%',x2:'0%',y2:'100%'});
  area.append(node('stop',{offset:'0%','stop-color':'#2e6eae','stop-opacity':'.28'}),node('stop',{offset:'100%','stop-color':'#90c1da','stop-opacity':'.02'}));defs.append(gradient,area);svg.append(defs);
  for(let i=0;i<=3;i++){const y=top+plotH*i/3;svg.append(node('line',{x1:left,y1:y,x2:w-right,y2:y,stroke:'#dce8f2','stroke-dasharray':'3 4'}),node('text',{x:left-9,y:y+4,'text-anchor':'end'},new Intl.NumberFormat('pt-PT',{maximumFractionDigits:0,notation:peak>9999?'compact':'standard'}).format(peak*(1-i/3))));}
  const coords=points.map((p,i)=>({x:points.length===1?left+plotW/2:left+i*plotW/(points.length-1),y:top+plotH*(1-p.value/peak),...p}));
  let d=`M${coords[0].x},${coords[0].y}`;
  for(let i=1;i<coords.length;i++){const previous=coords[i-1],current=coords[i],mid=(previous.x+current.x)/2;d+=` C${mid},${previous.y} ${mid},${current.y} ${current.x},${current.y}`;}
  if(coords.length>1){svg.append(node('path',{d:`${d} L${coords.at(-1).x},${top+plotH} L${coords[0].x},${top+plotH} Z`,fill:'url(#epal-area-gradient)'}),node('path',{d,fill:'none',stroke:'url(#epal-line-gradient)','stroke-width':4,'stroke-linecap':'round','stroke-linejoin':'round'}));}
  coords.forEach((p,i)=>{const a=node('a',{href:p.href,'data-drill':'','data-tip':p.tip,tabindex:0,'aria-label':p.tip});a.append(node('circle',{cx:p.x,cy:p.y,r:5,fill:i===coords.length-1?'#41e4c0':'#fff',stroke:'#2e6eae','stroke-width':3}),node('title',{},p.tip));svg.append(a);if(points.length<=12||i%Math.ceil(points.length/10)===0)svg.append(node('text',{x:p.x,y:h-9,'text-anchor':'middle'},p.label));});
  const container=document.createElement('div');container.className='epal-line-chart';container.append(svg);host.append(container);host.classList.add('epal-has-line');
})();
