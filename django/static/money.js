/* Decimal calculations mirror TotalsCalculator. Values are kept as scaled integers. */
(function (root) {
  'use strict';
  const rounded = (n, d) => (n + d / 2n) / d;
  function decimal(value) {
    const number = Number(String(value ?? 0).replace(',', '.'));
    if (!Number.isFinite(number) || number < 0 || number > 1e8) throw new Error('Valeur numérique invalide');
    const [whole, fraction] = number.toFixed(4).split('.');
    return BigInt(whole) * 10000n + BigInt(fraction);
  }
  function line(input) {
    const quantity = decimal(input.quantity), price = decimal(input.price);
    const discount = decimal(input.discount), rate = decimal(input.rate);
    if (discount > 1000000n || rate > 1000000n) throw new Error('Pourcentage invalide');
    const base = rounded(quantity * price, 1000000n);
    const allowance = rounded(base * discount, 1000000n);
    const net = base - allowance;
    return { base: Number(base) / 100, discount: Number(allowance) / 100, net: Number(net) / 100,
      vat: Number(rounded(net * rate, 1000000n)) / 100, rate: Number(rate) / 10000 };
  }
  function calculate(inputs) {
    const lines = inputs.map(line), groups = new Map();
    lines.forEach((item, index) => {
      const key = String(item.rate), rate = decimal(item.rate), net = BigInt(Math.round(item.net * 100));
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push({ index, net, rate, tax: net * rate / 1000000n, fraction: net * rate % 1000000n });
    });
    groups.forEach(members => {
      const basis = members.reduce((sum, item) => sum + item.net, 0n);
      const total = rounded(basis * members[0].rate, 1000000n);
      const rest = total - members.reduce((sum, item) => sum + item.tax, 0n);
      members.sort((a, b) => Number(b.fraction - a.fraction) || a.index - b.index);
      members.forEach((item, rank) => { lines[item.index].vat = Number(item.tax + (BigInt(rank) < rest ? 1n : 0n)) / 100; });
    });
    const totals = { base: 0, discount: 0, net: 0, vat: 0 };
    for (const key of Object.keys(totals)) totals[key] = lines.reduce((sum, item) => sum + Math.round(item[key] * 100), 0) / 100;
    return { lines, ...totals, gross: (Math.round(totals.net * 100) + Math.round(totals.vat * 100)) / 100 };
  }
  root.ZeMoney = { line, calculate };
  if (typeof module !== 'undefined') module.exports = root.ZeMoney;
})(typeof window !== 'undefined' ? window : globalThis);
