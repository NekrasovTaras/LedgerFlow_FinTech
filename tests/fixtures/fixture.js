const disclosure = document.querySelector('details');
const status = document.querySelector('#interaction-status');

disclosure.addEventListener('toggle', () => {
  status.textContent = `Scenario details are ${disclosure.open ? 'open' : 'closed'}.`;
});
