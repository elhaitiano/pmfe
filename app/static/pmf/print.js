(() => {
  const data = JSON.parse(document.getElementById('distribution-data').textContent);
  function drawChart(canvas) {
    const context = canvas.getContext('2d');
    context.clearRect(0, 0, canvas.width, canvas.height);
    const total = data.reduce((sum, item) => sum + item.hours, 0);
    let angle = -Math.PI / 2;
    if (!total) {context.beginPath(); context.arc(110,110,78,0,2*Math.PI); context.fillStyle='#d9e2ec'; context.fill();}
    for (const item of data) {
      const end = angle + item.hours / total * Math.PI * 2;
      context.beginPath(); context.moveTo(110,110); context.arc(110,110,78,angle,end); context.closePath(); context.fillStyle=item.color; context.fill(); angle=end;
    }
    context.beginPath(); context.arc(110,110,43,0,2*Math.PI); context.fillStyle='#fff'; context.fill();
    const label = total.toLocaleString('pt-PT', {minimumFractionDigits:1, maximumFractionDigits:1});
    context.fillStyle = '#073b7a';
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    let size = 28;
    context.font = 'bold ' + size + 'px Arial';
    while (context.measureText(label).width > 74 && size > 10) {
      size -= 1;
      context.font = 'bold ' + size + 'px Arial';
    }
    context.fillText(label, 110, 104);
    context.font = '12px Arial';
    context.fillText('HORAS', 110, 129);
  }
  function paginate() {
    const host = document.getElementById('pages'), original = host.querySelector('.page');
    const header = original.querySelector('.header'), tableSection = original.querySelector('.table-section');
    const signature = original.querySelector('.final-block'), footer = original.querySelector('.footer');
    const name = original.querySelector('.photo-label').textContent;
    const rows = [...tableSection.querySelectorAll('tbody > tr')];
    const firstBlocks = [...original.children].filter(x => x !== tableSection && x !== signature && x !== footer);
    host.replaceChildren();
    const createPage = first => {
      const page = document.createElement('section'); page.className='page'+(first?' first-page':' continuation-page');
      host.append(page);
      if (first) firstBlocks.forEach(x => page.append(x.cloneNode(true)));
      else {
        page.append(header.cloneNode(true));
        const note=document.createElement('p'); note.className='continuation'; note.textContent='Continuação · '+name; page.append(note);
      }
      page.append(footer.cloneNode(true)); return page;
    };
    const addTable = page => {
      const section=tableSection.cloneNode(true); section.querySelector('tbody').replaceChildren();
      page.insertBefore(section,page.querySelector('.footer')); return section;
    };
    const fits = (page, element) => element.getBoundingClientRect().bottom <= page.querySelector('.footer').getBoundingClientRect().top - 12;
    let page=createPage(true), section=addTable(page);
    for (const row of rows) {
      section.querySelector('tbody').append(row.cloneNode(true));
      if (!fits(page,section)) {
        const last=section.querySelector('tbody').lastElementChild; last.remove();
        if (!section.querySelector('tbody').children.length) section.remove();
        page=createPage(false); section=addTable(page); section.querySelector('tbody').append(last);
      }
    }
    let final=signature.cloneNode(true); page.insertBefore(final,page.querySelector('.footer'));
    if (!fits(page,final)) {
      final.remove();
      const trailing=section.querySelector('tbody').lastElementChild;
      const keepContext=section.querySelector('tbody').children.length>1;
      if (keepContext) trailing.remove();
      page=createPage(false);
      if (keepContext) {section=addTable(page);section.querySelector('tbody').append(trailing);}
      page.insertBefore(final,page.querySelector('.footer'));
    }
    const pages=[...host.querySelectorAll('.page')];
    pages.forEach((x,i)=>{x.querySelector('.page-counter').textContent='Pág. '+(i+1)+'/'+pages.length; x.querySelectorAll('canvas').forEach(drawChart);});
    document.body.dataset.printReady='true';
  }
  window.pmfPrintReady = new Promise(resolve => {
    const ready=async()=>{await document.fonts.ready; paginate(); resolve();};
    if (document.readyState==='complete') ready(); else window.addEventListener('load',ready,{once:true});
  });
})();
