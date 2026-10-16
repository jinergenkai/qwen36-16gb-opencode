function render() {
  const total = window.ITEMS.reduce((s, i) => s + i.price * i.qty, 0);
  document.getElementById('total').textContent = 'Total: ' + total;
}
window.addEventListener('load', render);
