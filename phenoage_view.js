/* Render the published model only; legacy cached scores must never look like PhenoAge. */
function renderPhenoAge(data) {
  const published = data.model_version === 'clinical-phenoage-levine2018-v1';
  const model = published ? data.phenoage : null;
  const available = model?.status === 'available' && Number.isFinite(model.estimated_age);
  const summary = [
    ['Current Age', published && data.chronological_age != null ? data.chronological_age : '—'],
    ['Clinical PhenoAge', available ? `${model.estimated_age} years` : 'Unavailable'],
    ['Age at Blood Draw', model?.age_at_collection ?? '—'],
    ['Difference at Blood Draw', available ? `${model.age_difference >= 0 ? '+' : ''}${model.age_difference} years` : '—'],
    ['Required Lab Inputs', model ? `${model.ready_count} / ${model.required_count}` : '—'],
    ['Blood Draw Date', model?.collection_date || '—']
  ];
  const root = document.getElementById('summary-stats'); root.replaceChildren();
  for (const [label, value] of summary) {
    const card = document.createElement('div'); card.className = 'stat';
    for (const [className, text] of [['label', label], ['val', value]]) {
      const child = document.createElement('div'); child.className = className; child.textContent = text; card.append(child);
    }
    root.append(card);
  }
  document.getElementById('phenoage-status').textContent = model
    ? `${available ? 'Calculated' : 'Not calculated'}${model.collection_date ? ` for ${model.collection_date} · ${model.lab_source}` : ''}. ${(model.reasons || []).join(' ')}`
    : 'This cached snapshot uses the retired custom model. Start or restart the app server to load Clinical PhenoAge; no legacy age estimate is shown.';
  document.getElementById('phenoage-policy').textContent = model?.selection_policy || '';
  document.getElementById('phenoage-interpretation').textContent = model?.interpretation || '';
  const inputs = document.getElementById('phenoage-inputs'); inputs.replaceChildren();
  for (const input of model?.inputs || []) {
    const row = document.createElement('tr');
    const value = input.value == null ? '—' : `${Number(input.value.toPrecision(6))} ${input.unit}`;
    for (const text of [input.label, value, input.status === 'ready' ? 'Ready' : input.detail]) {
      const cell = document.createElement('td'); cell.textContent = text; row.append(cell);
    }
    inputs.append(row);
  }
  const history = document.getElementById('phenoage-history'); history.replaceChildren();
  const previous = (model?.history || []).filter(item => item.collection_date !== model.collection_date || item.lab_source !== model.lab_source);
  for (const item of previous) {
    const paragraph = document.createElement('p');
    paragraph.textContent = `${item.collection_date} · ${item.lab_source}: PhenoAge ${item.estimated_age} years; age at draw ${item.age_at_collection}; difference ${item.age_difference} years.`;
    history.append(paragraph);
  }
  if (!previous.length) history.textContent = 'No previous complete assessments.';
}
