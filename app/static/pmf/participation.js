(() => {
  const workspace = document.querySelector('[data-coverage-workspace]');
  if (!workspace) return;
  let opener;
  workspace.addEventListener('click', event => {
    const open = event.target.closest('[data-coverage-open]');
    const close = event.target.closest('[data-coverage-close]');
    if (open) {
      const dialog = document.getElementById(open.dataset.coverageOpen);
      if (dialog && !dialog.open) { opener = open; dialog.showModal(); }
    }
    if (close) close.closest('dialog')?.close();
  });
  workspace.querySelectorAll('dialog').forEach(dialog => {
    dialog.addEventListener('close', () => { opener?.focus(); });
    if (dialog.hasAttribute('data-open-on-load')) dialog.showModal();
  });
})();
