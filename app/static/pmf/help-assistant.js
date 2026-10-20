(function () {
  'use strict';
  function normalise(value) { return String(value).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim().replace(/\bplataform\b/g, 'plataforma').replace(/\b(?:manegear|manegiar|manejar)\b/g, 'manusear'); }
  function rank(question, guides) {
    const q = normalise(question);
    if (!q) return [];
    return guides.map(guide => {
      let score = 0;
      for (const key of guide.keys) {
        const k = normalise(key);
        if ((' ' + q + ' ').includes(' ' + k + ' ')) score += k.split(' ').length * 3;
      }
      return {guide, score};
    }).filter(item => item.score > 0).sort((a,b) => b.score-a.score);
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {normalise, rank};
  if (typeof document === 'undefined') return;
  const panel = document.getElementById('epalHelpPanel');
  if (!panel) return;
  const toggle = document.getElementById('epalHelpToggle');
  const close = document.getElementById('epalHelpClose');
  const question = document.getElementById('epalHelpQuestion');
  const messages = document.getElementById('epalHelpMessages');
  const suggestions = document.getElementById('epalHelpSuggestions');
  const route = panel.dataset.route;
  const staff = panel.dataset.staff === 'true';
  let guides = [], active = null, step = 0, loaded = false, loading = null, marked = null;
  const storageKey = 'pmf.help.guide.v1';
  const read = () => {try {return JSON.parse(sessionStorage.getItem(storageKey));} catch (_) {return null;}};
  const save = value => {try {if(value) sessionStorage.setItem(storageKey, JSON.stringify(value)); else sessionStorage.removeItem(storageKey);} catch (_) {}};
  function clearMark() { if(marked) marked.classList.remove('epal-help-highlight'); marked = null; }
  function element(tag, text, cls) {const el=document.createElement(tag); if(text) el.textContent=text; if(cls) el.className=cls; return el;}
  function button(text, fn) {const el=element('button',text); el.type='button'; el.addEventListener('click',fn); return el;}
  function append(text, cls) {messages.append(element('p', text, cls)); while(messages.children.length>12) messages.firstElementChild.remove(); messages.scrollTop=messages.scrollHeight;}
  function shortcuts() {
    suggestions.replaceChildren();
    const contextual = route.startsWith('group_') || route.startsWith('course_') || route === 'planning' ? ['planning','enrol','list'] : route.startsWith('executive') ? ['report','dashboard','pdf_error'] : route.startsWith('batch') ? ['results','enrol','list'] : route.startsWith('employee') ? ['profile','results','list'] : route === 'dashboard' ? ['dashboard','report','pdf_error'] : ['planning','list','profile'];
    const ids = [...new Set(['overview',...contextual,...guides.map(g=>g.id)])];
    for (const id of ids) {const guide=guides.find(g=>g.id===id); if(!guide)continue; suggestions.append(button(guide.title,()=>start(guide)));}
  }
  function showStep() {
    clearMark(); messages.replaceChildren();
    if(!active) return;
    append(active.title,'epal-help-title');
    if(active.staff && !staff) append('Esta operação exige acesso de administrador. Pode consultar a orientação e pedir ao responsável que efectue o registo.','epal-help-notice');
    const current=active.steps[step];
    append('Passo '+(step+1)+' de '+active.steps.length,'epal-help-progress');
    append(current.text);
    const actions=element('div',null,'epal-help-actions');
    if(current.target) actions.append(button('Mostrar onde clicar',()=>{
      clearMark();
      let candidates;try{candidates=Array.from(document.querySelectorAll(current.target)).filter(el=>!panel.contains(el));}catch(_){append('O controlo indicado precisa de ser corrigido pelo administrador.');return;}
      const target=candidates.find(el=>el.getClientRects().length && !el.disabled);
      if(!target) {append('Este controlo não está disponível nesta página ou com o seu acesso. Abra a página indicada e siga o passo; a ajuda não executa a operação.','epal-help-notice'); return;}
      marked=target; target.classList.add('epal-help-highlight'); target.scrollIntoView({behavior:'smooth',block:'center'});
      append('O controlo ficou destacado na página. Feche a ajuda se precisar de espaço para o utilizar.');
    }));
    const link=panel.querySelector('[data-help-route="'+active.route+'"]');
    if(link && route!==active.route) {const a=element('a','Abrir '+link.textContent); a.href=link.href; actions.append(a);}
    if(step>0) actions.append(button('Anterior',()=>{step--;save({id:active.id,step,open:true});showStep();}));
    if(step<active.steps.length-1) actions.append(button('Seguinte',()=>{step++;save({id:active.id,step,open:true});showStep();}));
    else actions.append(button('Concluir orientação',()=>{active=null;save(null);clearMark();messages.replaceChildren();append('Orientação concluída. Verifique na plataforma se guardou os dados ou descarregou o documento pretendido.');}));
    actions.append(button('Outros assuntos',()=>{active=null;save(null);clearMark();messages.replaceChildren();append('Escolha um assunto abaixo ou escreva outra pergunta.');}));
    messages.append(actions);
  }
  function start(guide) {active=guide;step=0;save({id:guide.id,step,open:true});showStep();}
  async function load() {
    if(loaded) return;
    if(loading) return loading;
    loading=(async()=>{
      guides=[];active=null;suggestions.replaceChildren();
      try {
        const response=await fetch(panel.dataset.guides,{credentials:'same-origin'});
        if(!response.ok) throw new Error('load');
        guides=await response.json(); active=null;step=0;loaded=true;shortcuts();
        const previous=read();
        if(previous && previous.id) {
          active=guides.find(g=>g.id===previous.id);
          if(active) {step=Math.min(Math.max(0,Number(previous.step)||0),active.steps.length-1);showStep();return;}
        }
        append('Escolha um assunto ou escreva uma pergunta. Os guias utilizam as funções desta versão da plataforma.');
      } catch(_) {append('Não foi possível carregar os guias locais. Actualize a página; pode utilizar os atalhos abaixo.');}
      finally {loading=null;}
    })();
    return loading;
  }
  function hide() {panel.hidden=true;toggle.setAttribute('aria-expanded','false');clearMark();if(active) save({id:active.id,step,open:false});toggle.focus();}
  async function open() {if(active)save({id:active.id,step,open:true});loaded=false;panel.hidden=false;messages.replaceChildren();toggle.setAttribute('aria-expanded','true');await load();question.focus();}
  toggle.addEventListener('click',()=>panel.hidden?open():hide()); close.addEventListener('click',hide);
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!panel.hidden)hide();});
  document.getElementById('epalHelpForm').addEventListener('submit',async event=>{
    event.preventDefault(); await load(); const text=question.value.trim(); if(!text)return; question.value='';
    const matches=rank(text,guides); clearMark(); active=null;save(null);messages.replaceChildren(); append(text,'epal-help-user');
    if(!matches.length){append('Não encontrei um guia seguro para essa pergunta. Escolha um dos assuntos abaixo ou explique a tarefa com outras palavras. Não consigo consultar registos de pessoas nem diagnosticar um erro sem a mensagem técnica.');return;}
    if(matches.length===1 || matches[0].score >= matches[1].score+3){start(matches[0].guide);return;}
    append('Encontrei estas orientações. Escolha a que corresponde à sua tarefa:');
    for(const item of matches.slice(0,3))messages.append(button(item.guide.title,()=>start(item.guide)));
  });
  const previous=read();if(previous && previous.open)open();
})();
