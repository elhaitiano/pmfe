const menuButton = document.getElementById('sidebarToggle');
const sidebar = document.getElementById('mainSidebar');
menuButton?.addEventListener('click', () => {
  const opened = sidebar.classList.toggle('pmf-open');
  menuButton.setAttribute('aria-expanded', String(opened));
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    sidebar?.classList.remove('pmf-open');
    menuButton?.setAttribute('aria-expanded', 'false');
  }
});
