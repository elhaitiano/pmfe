(() => {
  'use strict';
  const root = document.getElementById('viewAvaliacao');
  if (!root) return;
  const rows = Array.from(root.querySelectorAll('[data-competency]'));
  const detail = root.querySelector('#compDetail');
  const controls = root.querySelector('[data-radar-controls]');
  const overview = root.querySelector('#radarOverviewBtn');
  const scan = root.querySelector('#radar360Btn');
  const cycle = root.querySelector('#profile-cycle');
  const previous = root.querySelector('#profile-previous');
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  let timer = null;
  let selected = -1;

  function stop() {
    if (timer !== null) window.clearInterval(timer);
    timer = null;
    if (scan) {
      scan.setAttribute('aria-pressed', 'false');
      scan.textContent = reduced.matches ? 'Próxima competência · 360°' : 'Visão 360°';
    }
  }

  function select(index) {
    selected = index;
    rows.forEach((row, i) => {
      row.classList.toggle('active', i === index);
      row.setAttribute('aria-pressed', String(i === index));
    });
    root.querySelectorAll('[data-axis]').forEach(axis => {
      axis.classList.toggle('active', Number(axis.dataset.axis) === index + 1);
    });
    if (detail && rows[index]) detail.textContent = rows[index].textContent.trim().replace(/\s+/g, ' ');
  }

  rows.forEach((row, index) => row.addEventListener('click', () => {
    stop();
    select(index);
  }));
  if (cycle && previous) cycle.addEventListener('change', () => { previous.value = 'none'; });
  if (!controls || !rows.length) return;
  controls.hidden = false;
  stop();
  overview.addEventListener('click', () => {
    stop();
    select(-1);
    detail.textContent = 'Visão global. Seleccione uma competência para consultar os resultados e a meta.';
  });
  scan.addEventListener('click', () => {
    if (timer !== null) { stop(); return; }
    const next = () => select((selected + 1) % rows.length);
    next();
    if (!reduced.matches) {
      timer = window.setInterval(next, 1800);
      scan.textContent = 'Parar visão 360°';
      scan.setAttribute('aria-pressed', 'true');
    }
  });
  document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); });
  window.addEventListener('pagehide', stop);
})();
