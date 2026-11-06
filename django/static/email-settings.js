(() => {
  const provider = document.getElementById('mail-provider');
  if (!provider) return;
  function update() {
    document.querySelectorAll('[data-mail-provider]').forEach(group => {
      const active = group.dataset.mailProvider.split(' ').includes(provider.value);
      group.hidden = !active;
      group.querySelectorAll('input,select').forEach(input => { input.disabled = !active; });
    });
  }
  provider.addEventListener('change', () => {
    const client = document.querySelector('input[name=client_id]');
    const secret = document.querySelector('input[name=client_secret]');
    if (client) client.value = '';
    if (secret) secret.value = '';
    update();
  });
  update();
})();
