(() => {
  const month = document.querySelector('#history-month');
  const result = document.querySelector('#history-result');
  const rows = [...document.querySelectorAll('.history-days > li')];
  if (!month || !result) return;
  function update() {
    const totals = {confirmed: 0, operated: 0, cancelled: 0, returned: 0, missing: 0};
    let count = 0;
    for (const row of rows) {
      const data = row.dataset;
      const match = (!month.value || month.value === data.month) &&
        (!result.value || (result.value === 'missing' ? Number(data.missing) > 0 :
          Number(data.cancelled) + Number(data.returned) > 0));
      row.hidden = !match;
      if (!match) continue;
      count++;
      for (const key of Object.keys(totals)) totals[key] += Number(data[key]);
    }
    document.querySelector('#history-total').textContent =
      `${count}日間・結果確認済み${totals.confirmed}便：運航${totals.operated}便・欠航${totals.cancelled}便・引き返し${totals.returned}便（結果未取得${totals.missing}便）`;
    document.querySelector('#history-empty').hidden = count !== 0;
  }
  month.addEventListener('change', update);
  result.addEventListener('change', update);
  update();
})();
