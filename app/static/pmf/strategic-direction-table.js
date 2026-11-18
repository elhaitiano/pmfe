(() => {
 'use strict';
 const root=document.querySelector('.epal-strategic-table');if(!root)return;
 const rows=[...root.querySelectorAll('[data-strategic-unit]')],buttons=[...root.querySelectorAll('[data-unit-filter]')];
 const empty=root.querySelector('[data-unit-empty]'),footer=root.querySelector('.epal-unit-pagination'),range=root.querySelector('[data-unit-range]'),previous=root.querySelector('[data-unit-page=previous]'),next=root.querySelector('[data-unit-page=next]');
 const normal=s=>s.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase();
 // Semantic icons use the locally hosted Material Symbols font; no external requests.
 const identities=[
  [/fiscal/, 'fact_check','indigo','Fiscaliza??o e supervis?o'],
  [/comissao de servico/, 'assignment_ind','teal','Comiss?o de Servi?o'],
  [/conselho de administracao/, 'account_balance','indigo','Administra??o e governa??o'],
  [/cooperativa/, 'handshake','teal','Coopera??o'],
  [/compras|logistica/, 'local_shipping','blue','Compras e log?stica'],
  [/negocios|dividas/, 'request_quote','indigo','Neg?cios e recupera??o de d?vidas'],
  [/comercial/, 'storefront','blue','Actividade comercial'],
  [/administracao|patrimonio/, 'domain','blue','Administra??o e patrim?nio'],
  [/adutoras/, 'valve','cyan','Adutoras e transporte de ?gua'],
  [/contabilidade|financas/, 'account_balance_wallet','indigo','Contabilidade e finan?as'],
  [/qualidade|laborator/, 'science','indigo','Controlo e qualidade da ?gua'],
  [/activos|ativos|automacao/, 'precision_manufacturing','cyan','Gest?o de activos e automa??o'],
  [/manutencao/, 'build','cyan','Manuten??o'],
  [/engenharia/, 'engineering','blue','Engenharia'],
  [/organizacao|metodos/, 'schema','indigo','Organiza??o e m?todos'],
  [/producao|\betas?\b/, 'water_drop','blue','Produ??o e tratamento de ?gua'],
  [/recursos humanos/, 'groups','teal','Recursos humanos'],
  [/distribuicao|\bredes?\b/, 'hub','teal','Distribui??o e redes de ?gua'],
  [/tecnologia|informacao/, 'computer','cyan','Tecnologia e informa??o'],
  [/juridico|contencioso/, 'gavel','indigo','Assessoria jur?dica e contencioso'],
  [/auditoria/, 'policy','indigo','Auditoria interna'],
  [/marketing|relacoes instit/, 'campaign','teal','Comunica??o e rela??es institucionais'],
  [/seguranca/, 'shield','blue','Seguran?a empresarial'],
  [/projecto bita/, 'construction','cyan','Projecto Bita'],
  [/projecto proagua/, 'water','teal','Projecto Pro?gua']
 ];
 rows.forEach(row=>{
  const label=normal(row.dataset.unitLabel||'').replace(/\s+/g,' ').trim();
  row.dataset.unitKind=/\betas?\b/.test(label)?'eta':/\bredes?\b/.test(label)?'network':'other';
  const identity=identities.find(([pattern])=>pattern.test(label));
  const [,symbol,tone,meaning]=identity||[null,'corporate_fare','blue','Unidade organizacional'];
  const icon=row.querySelector('.epal-unit-icon');
  if(icon){icon.textContent=symbol;icon.dataset.tone=tone;icon.title=meaning;}
 });
 let filter='all',page=0;const size=4;
 function render(){const selected=rows.filter(row=>filter==='all'||row.dataset.unitKind===filter),pages=Math.ceil(selected.length/size);page=Math.max(0,Math.min(page,pages-1));const start=page*size,visible=new Set(selected.slice(start,start+size));rows.forEach(row=>{row.hidden=!visible.has(row);row.dataset.unitMatched=String(selected.includes(row));});if(empty)empty.hidden=selected.length!==0||rows.length===0;if(footer)footer.hidden=selected.length<=size;if(range)range.textContent=selected.length?`${start+1}–${Math.min(start+size,selected.length)} de ${selected.length} unidades`:'Sem unidades';if(previous)previous.disabled=page===0;if(next)next.disabled=page+1>=pages;buttons.forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.unitFilter===filter)));}
 buttons.forEach(button=>button.addEventListener('click',()=>{filter=button.dataset.unitFilter;page=0;render();}));previous?.addEventListener('click',()=>{page--;render();});next?.addEventListener('click',()=>{page++;render();});render();
})();
