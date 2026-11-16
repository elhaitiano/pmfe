(() => {
  // Administração e Definições: open and close the information dialogs.
  let opener = null;
  document.addEventListener('click', event => {
    const open = event.target.closest('[data-dialog-open]');
    if (open) {
      const dialog = document.getElementById(open.dataset.dialogOpen);
      if (dialog && !dialog.open) { opener = open; dialog.showModal(); }
      return;
    }
    const close = event.target.closest('[data-dialog-close]');
    if (close) close.closest('dialog')?.close();
  });
  document.querySelectorAll('dialog.ux-dialog').forEach(dialog => {
    dialog.addEventListener('close', () => { opener?.focus(); opener = null; });
    // Clicking the backdrop closes the dialog; Escape is handled by the browser.
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  });
})();
