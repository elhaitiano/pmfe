(() => {
  const form = document.getElementById('trainingForm');
  if (!form?.querySelector('#id_days')) return;
  const get = id => form.querySelector('#id_' + id);
  const read = id => get(id).value.trim() === '' ? null : Number(get(id).value.replace(',', '.'));
  const fmt = value => value === null ? '—' : value.toLocaleString('pt-PT', {maximumFractionDigits: 1});
  const update = () => {
    const days = read('days'), hours = read('hours_per_day'), attended = read('attendance');
    const status = get('status').value;
    const validDays = Number.isInteger(days) && days > 0;
    const validHours = hours !== null && hours >= .5 && hours <= 24;
    const validAttendance = attended !== null && Number.isInteger(attended) && attended >= 0 && validDays && attended <= days;
    document.getElementById('calcPlanned').textContent = validDays && validHours ? fmt(days * hours) + ' H' : '—';
    document.getElementById('calcCompleted').textContent = validAttendance && validHours ? fmt(attended * hours) + ' H' : '—';
    document.getElementById('calcAttendance').textContent = validAttendance && status !== 'Planeado' ? fmt(Math.round(attended / days * 1000) / 10) + '%' : '—';
    document.getElementById('calcAbsences').textContent = validAttendance && ['Concluído','Desistência'].includes(status) ? fmt(days - attended) : 'Por confirmar';
    document.getElementById('calcMessage').textContent = attended !== null && validDays && attended > days ? 'As presenças não podem ultrapassar a duração.' : status === 'Em Curso' ? 'Assiduidade provisória. As ausências são apuradas no encerramento.' : status === 'Planeado' ? 'Formação não iniciada. A assiduidade será calculada após o registo de presenças.' : 'Horas realizadas = presenças × horas por dia. Assiduidade = presenças ÷ dias de formação.';
  };
  ['days','hours_per_day','attendance','status'].forEach(id => get(id).addEventListener('input', update));
  update();
})();
