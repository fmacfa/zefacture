'use strict';
document.querySelectorAll('[data-confirm]').forEach(form => form.addEventListener('submit', event => {
  if (!window.confirm(form.dataset.confirm)) event.preventDefault();
}));
document.querySelectorAll('[data-back]').forEach(button => button.addEventListener('click', () => history.back()));
const current = location.pathname;
let active = null;
document.querySelectorAll('.sidebar nav a').forEach(link => {
  const href = new URL(link.href).pathname;
  if (current === href || href !== '/app' && current.startsWith(href + '/')) {
    if (!active || href.length > new URL(active.href).pathname.length) active = link;
  }
});
if (active) { active.classList.add('active'); active.setAttribute('aria-current', 'page'); }
