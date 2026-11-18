// UI state only. The server remains authoritative for calendars and eligibility.
(() => {
  'use strict';
  const dates = document.getElementById('id_dates');
  const start = document.getElementById('id_start_date');
  const end = document.getElementById('id_end_date');
  const mode = document.getElementById('id_calendar_mode');
  const summary = document.getElementById('planCalendarSummary');
  function calendar() {
    if (!dates || mode.value !== 'range') return;
    const first = new Date(start.value + 'T00:00:00Z');
    const last = new Date(end.value + 'T00:00:00Z');
    const count = (last - first) / 86400000 + 1;
    if (Number.isInteger(count) && count >= 1 && count <= 366) {
      dates.value = Array.from({length: count}, (_, i) => new Date(+first + i * 86400000).toISOString().slice(0, 10)).join('\n');
      summary.textContent = `${count} dias consecutivos propostos, incluindo fins-de-semana. Reveja antes de guardar.`;
    } else {
      dates.value = '';
      summary.textContent = 'Indique um início e um fim válidos; os dados serão validados ao guardar.';
    }
    dates.dispatchEvent(new Event('input', {bubbles: true}));
  }
  if (dates && start && end && mode) {
    [start, end].forEach(field => field.addEventListener('input', calendar));
    mode.addEventListener('change', calendar);
    // Editing the individual dates is an explicit choice; never overwrite gaps.
    dates.addEventListener('change', () => {mode.value = 'custom'; summary.textContent = 'Calendário personalizado — apenas os dias indicados serão guardados.';});
    calendar();
  }
  const course = document.getElementById('id_course');
  const excelSelect = document.getElementById('draftExcelSelect');
  const excelCalendar = document.getElementById('draftExcelCalendar');
  if (excelSelect && excelCalendar) {
    const allowed = [...excelSelect.options].map(o => o.value).filter(Boolean).sort();
    if (allowed.length) {excelCalendar.min = allowed[0]; excelCalendar.max = allowed[allowed.length - 1];}
    excelCalendar.value = excelSelect.value;
    excelSelect.addEventListener('change', () => {excelCalendar.value = excelSelect.value; excelCalendar.setCustomValidity('');});
    excelCalendar.addEventListener('input', () => {
      const valid = allowed.includes(excelCalendar.value);
      excelCalendar.setCustomValidity(valid || !excelCalendar.value ? '' : 'Escolha um dia do calendário da formação.');
      excelSelect.value = valid ? excelCalendar.value : '';
    });
  }
  if (course) {
    let numbers = {};
    try {numbers = JSON.parse(document.getElementById('planCourseNumbers').textContent);} catch (_) {}
    const update = () => {
      document.getElementById('planGroupTitle').textContent = 'Criar grupo — ' + (course.value ? course.selectedOptions[0].textContent : 'Escolher formação');
      document.getElementById('planPredictedGroup').textContent = numbers[course.value] ? 'G' + numbers[course.value] : 'A definir';
    };
    course.addEventListener('change', update); update();
  }
  const form = document.getElementById('draftSelection');
  if (!form) return;
  const key = 'pmf.draft.selection.' + form.dataset.scope;
  const saved = new Set(JSON.parse(document.getElementById('draftSavedIds').textContent));
  const boxes = [...form.querySelectorAll('[data-candidate]')];
  const count = document.getElementById('draftSelectedCount');
  let selected = new Set();
  try {
    const values = JSON.parse(sessionStorage.getItem(key) || '[]');
    if (Array.isArray(values)) selected = new Set(values.filter(x => typeof x === 'string' && /^\d{1,18}$/.test(x) && !saved.has(x)).slice(0,1000));
  } catch (_) {}
  function sync() {
    boxes.forEach(box => {
      if (!box.disabled) {
        if (box.checked) selected.add(box.value); else selected.delete(box.value);
      }
      box.closest('tr').classList.toggle('is-selected', box.checked && !box.disabled);
    });
    try {sessionStorage.setItem(key, JSON.stringify([...selected]));} catch (_) {}
    count.textContent = `${selected.size} formandos seleccionados`;
  }
  boxes.forEach(box => {
    if (!box.disabled) box.checked = selected.has(box.value);
    box.addEventListener('change', sync);
  });
  const clear = document.getElementById('draftClearSelection');
  clear.hidden = false;
  clear.addEventListener('click', () => {selected.clear(); boxes.filter(b => !b.disabled).forEach(b => {b.checked = false;}); sync();});
  form.addEventListener('submit', event => {
    sync();
    if (!selected.size) {event.preventDefault(); count.textContent = 'Seleccione pelo menos um formando.'; return;}
    const carry = document.getElementById('draftCarriedSelection');
    carry.replaceChildren();
    const shown = new Set(boxes.map(b => b.value));
    selected.forEach(value => {
      if (!shown.has(value)) {
        const input = document.createElement('input');
        input.type = 'hidden'; input.name = 'employees'; input.value = value; carry.append(input);
      }
    });
  });
  sync();
})();
