'use strict';
const refundForm = document.querySelector('[data-reimbursement]');
if (refundForm) {
  const status = refundForm.elements.reimbursement_status;
  function update() {
    const paid = status.value === 'paid';
    const input = refundForm.elements.paid_on;
    refundForm.querySelector('[data-paid-date]').hidden = !paid;
    input.disabled = !paid;
    input.required = paid;
  }
  status.addEventListener('change', update);
  update();
}
