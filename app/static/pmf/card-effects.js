(() => {
 'use strict';
 const selector='.exec-kpi-card,.exec-glass-box,.exec-mini,.exec-direction-card,.epal-quick-card,.gf-kpi,.gf-panel,.gf-result,.gf-edit-card,.plan-panel,.plan-course,.plan-review,.glass-panel,.evaluation-section,.eval-card,.eval-panel,.decision-metric,.achievement-card,.cr-kpis article,.cr-panel,.history-kpis>div,.history-chart,.history-results,.history-nominal,.pmfe-manual-card,.pmfe-manual-section,.performance-card';
 const iconSelector='.epal-quick-icon,.exec-icon,.eval-icon,.evaluation-section-icon,.epal-quick-card>.material-symbols-outlined:first-child,.exec-panel-heading h3>.material-symbols-outlined,.gf-kpi>.material-symbols-outlined';
 function enhance(root){
  const cards=[...(root.matches?.(selector)?[root]:[]),...root.querySelectorAll(selector)];
  cards.forEach(card=>{
   if(card.closest('#mainSidebar,.epal-print,.toolbar'))return;
   card.classList.add('pmfe-motion-card');
   card.querySelectorAll(iconSelector).forEach(icon=>{
    if(icon.closest(selector)!==card||icon.classList.contains('pmfe-motion-icon'))return;
    const color=getComputedStyle(icon).color;
    icon.style.setProperty('--pmfe-icon-active',color==='rgb(255, 255, 255)'?'#005bc0':color);
    icon.classList.add('pmfe-motion-icon');
   });
  });
 }
 const main=document.querySelector('main');if(!main)return;enhance(main);
 new MutationObserver(records=>records.forEach(record=>record.addedNodes.forEach(node=>{if(node.nodeType===1)enhance(node)}))).observe(main,{childList:true,subtree:true});
})();
