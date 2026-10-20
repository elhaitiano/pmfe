// Presentation only. The signed server preview is authoritative and works without JS.
(() => {
  const form = document.getElementById('correctionProposal');
  const output = document.getElementById('correctionTotal');
  if (!form || !output) return;
  const days = form.querySelector('#id_days'), hours = form.querySelector('#id_hours');
  function update() {
    const d = Number(days.value), raw = hours.value.trim().replace(',', '.');
    const h = Math.round(Number(raw) * 100);
    output.textContent = Number.isInteger(d) && d >= 1 && d <= 366 &&
      /^\d+(?:\.\d{1,2})?$/.test(raw) && h >= 50 && h <= 2400
      ? (d * h / 100).toLocaleString('pt-PT', {maximumFractionDigits:2}) + ' H' : 'Por confirmar';
  }
  [days, hours].forEach(field => field.addEventListener('input', update));
  update();
})();
