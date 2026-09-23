let editingId = null;
const form = document.getElementById('weight-form');
const stamp = document.getElementById('measured-at');
const weight = document.getElementById('weight');
const message = document.getElementById('message');
const save = document.getElementById('save');
function newEntry() {
  editingId = null;
  const now = new Date();
  const pad = value => String(value).padStart(2, '0');
  stamp.value = `${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`;
  weight.value = '';
  document.getElementById('form-title').textContent = 'New entry';
}
async function refresh() {
  const response = await fetch('/api/weight-data', {cache:'no-store'});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Could not load entries.');
  const tbody = document.getElementById('entries'); tbody.replaceChildren();
  for (const entry of [...data.rows].reverse()) {
    const row = document.createElement('tr');
    for (const text of [entry.measured_at.replace('T', ' '), entry.weight_lb]) { const cell = document.createElement('td'); cell.textContent = text; row.append(cell); }
    const cell = document.createElement('td'); const edit = document.createElement('button'); edit.textContent = 'Edit'; edit.type = 'button';
    edit.addEventListener('click', () => { editingId = entry.id; stamp.value = entry.measured_at; weight.value = entry.weight_lb; document.getElementById('form-title').textContent = 'Edit entry'; message.textContent = ''; stamp.focus(); });
    cell.append(edit); row.append(cell); tbody.append(row);
  }
  if (!data.rows.length) { const row = tbody.insertRow(); const cell = row.insertCell(); cell.colSpan = 3; cell.textContent = 'No daily entries yet.'; }
  renderWeightContext(document.getElementById('weight-context'), data.health_context);
}
form.addEventListener('submit', async event => {
  event.preventDefault(); save.disabled = true;
  try {
    const response = await fetch('/api/admin/weight-entry', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id:editingId, measured_at:stamp.value, weight_lb:Number(weight.value)})});
    if (response.status === 401) throw new Error('Sign in through the Admin page before saving.');
    const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Save failed.');
    newEntry(); message.textContent = 'Entry saved to the app’s weight file.';
    try { await refresh(); } catch (_) { message.textContent += ' Reload to refresh the history.'; }
  } catch (error) { message.textContent = error.message; }
  finally { save.disabled = false; }
});
document.getElementById('cancel').addEventListener('click', () => { newEntry(); message.textContent = ''; });
newEntry(); refresh().catch(() => { message.textContent = 'Could not load entries. Open this page through the running app server.'; });
