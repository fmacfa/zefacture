'use strict';

document.addEventListener('submit', event => {
  const message = event.submitter?.dataset.confirm || event.target.dataset.confirm;
  if (message && !window.confirm(message)) event.preventDefault();
});
document.querySelectorAll('[data-back]').forEach(button => button.addEventListener('click', () => history.back()));

const current = location.pathname;
let active = null;
document.querySelectorAll('.sidebar nav a').forEach(link => {
  const href = new URL(link.href).pathname;
  if (current === href || href !== '/app' && current.startsWith(href + '/')) {
    if (!active || href.length > new URL(active.href).pathname.length) active = link;
  }
});
if (active) {
  const section = active.closest('details');
  if (section) section.open = true;
  active.classList.add('active');
  active.setAttribute('aria-current', 'page');
}

const sidebar = document.querySelector('[data-sidebar]');
if (sidebar) {
  const toggle = sidebar.querySelector('[data-sidebar-toggle]');
  function setMenu(open) {
    sidebar.toggleAttribute('data-menu-open', open);
    toggle.setAttribute('aria-expanded', String(open));
  }
  sidebar.setAttribute('data-menu-ready', '');
  toggle.hidden = false;
  toggle.addEventListener('click', () => setMenu(!sidebar.hasAttribute('data-menu-open')));
  sidebar.addEventListener('keydown', event => {
    if (event.key === 'Escape' && sidebar.hasAttribute('data-menu-open')) {
      setMenu(false);
      toggle.focus();
    }
  });
}
