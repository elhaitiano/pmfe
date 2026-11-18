/* Selection only. The existing preview/confirmation service authorizes every request. */
(() => {
  const form = document.getElementById('group-selection');
  if (!form) return;
  const boxes = [...document.querySelectorAll('[data-select-group]')];
  const all = document.getElementById('select-page-groups');
  const status = document.getElementById('selection-count');
  function update() {
    const count = boxes.filter(box => box.checked).length;
    document.getElementById('validate-selected').disabled = count === 0 || count > 10;
    status.textContent = count > 10 ? 'Seleccione no máximo 10 grupos por operação, ou utilize Validar formação.' : `${count} grupo(s) seleccionado(s). A próxima página permite rever as alterações.`;
    all.checked = count > 0 && count === boxes.length;
    all.indeterminate = count > 0 && count < boxes.length;
  }
  all.addEventListener('change', () => { boxes.forEach(box => { box.checked = all.checked; }); update(); });
  boxes.forEach(box => box.addEventListener('change', update));
  update();
})();
