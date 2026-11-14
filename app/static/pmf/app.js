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

// Keep direct visits and external referrals inside the platform.
const backLink = document.getElementById('pmfBackLink');
backLink?.addEventListener('click', event => {
  if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  if (document.referrer && window.history.length > 1) {
    const previous = new URL(document.referrer);
    if (previous.origin === window.location.origin && previous.pathname !== '/entrar/') {
      event.preventDefault();
      window.history.back();
    }
  }
});
