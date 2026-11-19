'use strict';
(() => {
  const panel = document.querySelector('[data-password-strength]');
  const field = panel?.closest('form')?.querySelector('input[name=password][autocomplete=new-password]');
  if (!field) return;
  const meter = panel.querySelector('meter');
  const feedback = panel.querySelector('[data-password-feedback]');
  function update() {
    const value = field.value;
    panel.hidden = !value;
    if (!value) return;
    const normalized = value.toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '');
    const mapped = normalized.replace(/[@4]/g, 'a').replace(/[0]/g, 'o').replace(/[13!]/g, c => c === '3' ? 'e' : 'i');
    const common = /motdepasse|password|azerty|qwerty|123456|bienvenue|welcome|letmein|abcdefgh/.test(mapped) ||
      /^(\S{1,8})\1+$/.test(normalized) || new Set(normalized).size < 5;
    const words = normalized.trim().split(/[\s_-]+/).filter(word => word.length >= 3);
    const classes = [/[a-z]/,/[A-Z]/,/\d/,/[^\p{L}\p{N}]/u].filter(pattern => pattern.test(value)).length;
    let score = 2;
    let message = 'Moyenne. Allongez le mot de passe ou ajoutez plusieurs mots distincts.';
    if (common || value.length < 10) {
      score = 1;
      message = value.length < 10 ? 'Faible. Le minimum est de 10 caractères.' : 'Faible. Évitez les mots courants, les suites et les répétitions.';
    } else if (value.length >= 20 && (new Set(words).size >= 4 || classes >= 3)) {
      score = 4; message = 'Très robuste.';
    } else if (value.length >= 14 && (classes >= 3 || new Set(words).size >= 3)) {
      score = 3; message = 'Robuste.';
    }
    meter.value = score;
    feedback.textContent = message;
  }
  field.addEventListener('input', update);
  update();
})();
