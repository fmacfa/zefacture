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
  function complete() {
    loading.hidden = true;
    if (!image.naturalWidth) reader.querySelector('[data-render-error]').hidden = false;
  }
  image.addEventListener('load', complete, {once:true});
  image.addEventListener('error', complete, {once:true});
  if (image.complete) complete();
  else loading.hidden = false;
}
