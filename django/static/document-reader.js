'use strict';

const reader = document.querySelector('[data-reader]');
if (reader) {
  const stage = reader.querySelector('.reader-stage');
  const controls = reader.querySelector('.reader-zoom');
  const smaller = controls.querySelector('[data-step="-1"]');
  const larger = controls.querySelector('[data-step="1"]');
  const levels = [75, 100, 125, 150, 200];
  let index = 1;
  function zoom(to) {
    index = to;
    stage.dataset.zoom = String(levels[index]);
    controls.querySelector('output').textContent = levels[index] + ' %';
    smaller.disabled = index === 0;
    larger.disabled = index === levels.length - 1;
  }
  smaller.addEventListener('click', () => zoom(index - 1));
  larger.addEventListener('click', () => zoom(index + 1));
  controls.querySelector('[data-fit]').addEventListener('click', () => zoom(1));
  controls.hidden = false;
  const image = stage.querySelector('img');
  const loading = reader.querySelector('[data-reader-loading]');
  const error = reader.querySelector('[data-render-error]');
  function complete() {
    loading.hidden = true;
    error.hidden = Boolean(image.naturalWidth);
  }
  image.addEventListener('load', complete);
  image.addEventListener('error', complete);
  if (image.complete) complete();
  else loading.hidden = false;
  const pages = Number(reader.dataset.inlinePages);
  if (pages > 1) {
    let page = 1;
    const previous = reader.querySelector('[rel=prev]'), next = reader.querySelector('[rel=next]');
    function visit(event, number) {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      if (number < 1 || number > pages) return;
      page = number;
      error.hidden = true;
      loading.hidden = false;
      reader.querySelector('[data-reader-page]').textContent = page;
      image.alt = 'Page ' + page + ' sur ' + pages + ' du justificatif';
      image.src = reader.dataset.imageUrl + '?image=1&page=' + page;
      stage.scrollTo(0, 0);
      previous.hidden = page === 1;
      next.hidden = page === pages;
      previous.href = reader.dataset.imageUrl + '?page=' + Math.max(1, page - 1);
      next.href = reader.dataset.imageUrl + '?page=' + Math.min(pages, page + 1);
      if (event.currentTarget.hidden) (previous.hidden ? next : previous).focus();
    }
    previous.addEventListener('click', event => visit(event, page - 1));
    next.addEventListener('click', event => visit(event, page + 1));
  }
}
