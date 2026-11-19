'use strict';

// The worker imports invoices independently of this page. Keep filter inputs intact.
const inbox = document.querySelector('#reception-sync[data-pending]');
const results = document.querySelector('#document-results');
if (inbox && results) {
  let attempts = 0;
  async function refreshInbox() {
    try {
      const response = await fetch(location.href, {cache: 'no-store', mode: 'same-origin'});
      if (!response.ok || response.redirected) return;
      const updated = new DOMParser().parseFromString(await response.text(), 'text/html');
      const status = updated.querySelector('#reception-sync');
      const documents = updated.querySelector('#document-results');
      if (!status || !documents || status.dataset.company !== inbox.dataset.company) return;
      if (!status.hasAttribute('data-pending')) {
        inbox.replaceWith(status);
        results.replaceWith(documents);
        return;
      }
    } catch {
      // The manual refresh link also works when the network is unavailable.
    }
    attempts += 1;
    if (attempts < 24) window.setTimeout(refreshInbox, 5000);
  }
  window.setTimeout(refreshInbox, 3000);
}
