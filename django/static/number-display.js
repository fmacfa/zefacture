'use strict';
(() => {
  const root = document.documentElement;
  const groups = {none: '', space: '\u202f', comma: ',', dot: '.'};
  const points = {comma: ',', dot: '.'};
  const group = root.dataset.numberGroup;
  const decimal = root.dataset.numberDecimal;
  function format(value, grouping, separator) {
    const parts = /^([+-]?)(\d+)(?:\.(\d+))?$/.exec(String(value));
    if (!parts) return String(value ?? '');
    return parts[1] + parts[2].replace(/\B(?=(\d{3})+(?!\d))/g, groups[grouping]) +
      (parts[3] ? points[separator] + parts[3] : '');
  }
  function canonical(value) {
    const mark = groups[group];
    const normalized = group === 'space' ? value.replace(/[ \u00a0\u202f]/g, mark) : value.trim();
    const pair = normalized.split(points[decimal]);
    if (pair.length > 2 || (pair.length === 2 && !/^\d+$/.test(pair[1]))) return null;
    const whole = pair[0].replace(/^[+-]/, '');
    const pieces = mark ? whole.split(mark) : [whole];
    if (!/^\d+$/.test(pieces[0]) || (pieces.length > 1 &&
        (pieces[0].length > 3 || pieces.slice(1).some(piece => !/^\d{3}$/.test(piece))))) return null;
    return (pair[0].startsWith('-') ? '-' : '') + pieces.join('') + (pair[1] ? '.' + pair[1] : '');
  }
  window.ZeNumbers = {
    amount(value) { return format(Number(value).toFixed(2), group || 'space', decimal || 'comma'); }
  };
  const grouping = document.getElementById('number-grouping');
  const separator = document.getElementById('number-decimal');
  if (grouping && separator) {
    function preview(event) {
      if (grouping.value === separator.value) {
        if (event?.target === separator) grouping.value = 'none';
        else separator.value = grouping.value === 'dot' ? 'comma' : 'dot';
      }
      document.getElementById('number-preview').textContent = format('1234567.89', grouping.value, separator.value);
    }
    grouping.addEventListener('change', preview);
    separator.addEventListener('change', preview);
    preview();
  }
  if (!(group in groups) || !(decimal in points)) return;
  // Preserve the canonical input used by calculations, form submissions and exports.
  function enhance(original) {
    if (original.dataset.numberReady || !original.matches('input[type=number]') ||
        original.name === 'smtp_port' || original.disabled || original.readOnly) return;
    const visible = document.createElement('input');
    const validation = original.cloneNode(false);
    visible.type = 'text';
    visible.inputMode = 'decimal';
    visible.required = original.required;
    for (const attr of ['id','class','aria-label','aria-labelledby','aria-describedby','placeholder','title']) {
      if (original.hasAttribute(attr)) visible.setAttribute(attr, original.getAttribute(attr));
    }
    for (const attr of ['id','aria-label','aria-labelledby','aria-describedby','title']) original.removeAttribute(attr);
    original.setAttribute('aria-hidden','true');
    original.dataset.numberReady = '1';
    original.type = 'hidden';
    visible.value = format(original.value, group, decimal);
    visible.addEventListener('input', () => {
      const result = visible.value === '' ? '' : canonical(visible.value);
      validation.value = result ?? '';
      visible.setCustomValidity(result === null ? 'Vérifiez les séparateurs du nombre.' :
        (validation.validity.valid ? '' : validation.validationMessage));
      original.value = result ?? '';
      original.dispatchEvent(new Event('input', {bubbles:true}));
    });
    visible.addEventListener('blur', () => {
      if (original.value && visible.validity.valid) visible.value = format(original.value,group,decimal);
    });
    original.before(visible);
  }
  function scan(node) {
    if (!(node instanceof Element)) return;
    if (node.matches('input[type=number]')) enhance(node);
    else node.querySelectorAll('input[type=number]').forEach(enhance);
  }
  document.addEventListener('DOMContentLoaded', () => {
    scan(document.body);
    new MutationObserver(changes => changes.forEach(change => change.addedNodes.forEach(scan)))
      .observe(document.body, {childList:true,subtree:true});
  });
})();
