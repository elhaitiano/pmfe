
(function(){
  if(window.__pmfChartTooltipReady) return;
  const tip=document.getElementById('pmfChartTooltip');
  if(!tip) return;
  window.__pmfChartTooltipReady=true;

  const kicker=tip.querySelector('.tt-kicker');
  const title=tip.querySelector('.tt-title');
  const body=tip.querySelector('.tt-body');
  let active=null;

  const fill=(el)=>{
    kicker.textContent=el.getAttribute('data-tip-kicker')||'Detalhes';
    title.textContent=el.getAttribute('data-tip-title')||'';
    const details=document.getElementById(el.getAttribute('data-tip-id'));
    body.replaceChildren(details ? details.content.cloneNode(true) : document.createTextNode('Sem informações adicionais.'));
  };

  const place=(x,y)=>{
    const pad=14, gap=18;
    tip.style.left='0px';
    tip.style.top='0px';
    const r=tip.getBoundingClientRect();
    let left=x+gap, top=y+gap;
    if(left+r.width>window.innerWidth-pad) left=x-r.width-gap;
    if(top+r.height>window.innerHeight-pad) top=y-r.height-gap;
    left=Math.max(pad,Math.min(left,window.innerWidth-r.width-pad));
    top=Math.max(pad,Math.min(top,window.innerHeight-r.height-pad));
    tip.style.left=left+'px';
    tip.style.top=top+'px';
  };

  const show=(el,x,y)=>{
    active=el;
    fill(el);
    tip.classList.add('show');
    tip.setAttribute('aria-hidden','false');
    requestAnimationFrame(()=>place(x,y));
  };

  const hide=()=>{
    active=null;
    tip.classList.remove('show');
    tip.setAttribute('aria-hidden','true');
  };

  document.addEventListener('pointerover',(e)=>{
    const el=e.target.closest?.('.chart-hover');
    if(!el) return;
    show(el,e.clientX,e.clientY);
  });

  document.addEventListener('pointermove',(e)=>{
    if(active) place(e.clientX,e.clientY);
  });

  document.addEventListener('pointerout',(e)=>{
    if(!active) return;
    const from=e.target.closest?.('.chart-hover');
    const to=e.relatedTarget;
    if(from===active && !(to && active.contains(to))) hide();
  });

  document.addEventListener('focusin',(e)=>{
    const el=e.target.closest?.('.chart-hover');
    if(!el) return;
    const r=el.getBoundingClientRect();
    show(el,r.left+r.width/2,r.top+r.height/2);
  });

  document.addEventListener('focusout',(e)=>{
    if(active && e.target.closest?.('.chart-hover')===active) hide();
  });

  // Clique/toque também mantém os detalhes visíveis em dispositivos sem hover.
  document.addEventListener('click',(e)=>{
    const el=e.target.closest?.('.chart-hover');
    if(!el) return;
    const r=el.getBoundingClientRect();
    show(el,r.left+r.width/2,r.top+r.height/2);
  });

  window.addEventListener('scroll',hide,{passive:true});
  window.addEventListener('resize',hide,{passive:true});
})();

