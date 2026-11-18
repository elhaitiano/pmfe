(() => {
 'use strict';
 const links=[...document.querySelectorAll('#mainSidebar .pmfe-main-item')];
 if(!links.length)return;
 const path=location.pathname;
 let id='navDashboard';
 if(/^\/(indicadores|avaliacao|competencias|desenvolvimento|minhas-avaliacoes)(\/|$)/.test(path))id='navGestao';
 else if(/^\/(administracao|admin|auditoria|validacao)(\/|$)/.test(path))id='navAdministracao';
 else if(/^\/assistente(\/|$)/.test(path))id='navAssistente';
 else if(/^\/consolidacao\/(historicos|participacoes|edicoes)/.test(path)||/^\/funcionarios\/\d+\/$/.test(path)||/^\/historico(\/|$)/.test(path))id='navHistorico';
 else if(/^\/(formandos|funcionarios)(\/|$)/.test(path))id='navFicha';
 else if(path==='/'||/^\/(planeamento|formadores|formacoes|certificados)(\/|$)/.test(path))id='navGestao';
 const selected=document.getElementById(id);
 if(selected){links.forEach(a=>a.removeAttribute('aria-current'));selected.setAttribute('aria-current','page');}
})();
