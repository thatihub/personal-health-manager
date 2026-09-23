function renderWeightContext(target, context) {
  target.replaceChildren();
  const add = text => { const p = document.createElement('p'); p.textContent = text; target.append(p); };
  if (context?.error) { add(context.error); return; }
  if (!context) { add('Current weight context unavailable. Open the app through its local server.'); return; }
  add(context.weight_lb == null ? 'No weight measurements yet.' : `${context.weight_lb} lb · ${context.date} · ${context.source}`);
  add(context.bmi == null ? 'BMI unavailable: weight and recorded height are required.' : `Current BMI: ${context.bmi} · height ${context.height_in} in from scan dated ${context.height_date}`);
  if (context.change_lb != null) add(`Daily weight change since ${context.change_since}: ${context.change_lb > 0 ? '+' : ''}${context.change_lb} lb`);
  for (const scan of context.latest_scans || []) {
    const values = [['body_fat_pct','Body fat','%'],['lean_body_mass_lb','Lean mass',' lb'],['skeletal_muscle_mass_lb','Skeletal muscle',' lb'],['basal_metabolic_rate_kcal','BMR',' kcal'],['visceral_fat_area_cm2','Visceral fat area',' cm²'],['bmd_t_score','Bone T-score','']].filter(([key]) => scan[key] != null).map(([key,label,unit]) => `${label}: ${scan[key]}${unit}`);
    add(`${scan.scan_type || 'Body scan'} · ${scan.date}: ${values.join(' · ')}`);
    if (scan.notes) add(`Scan notes: ${scan.notes}`);
  }
  add(context.age_model_note);
}
async function loadWeightContext() {
  const target = document.getElementById('weight-context');
  if (!target) return;
  try { const response = await fetch('/api/weight-data', {cache:'no-store'}); if (!response.ok) throw new Error(); const data = await response.json(); renderWeightContext(target, data.health_context); }
  catch (_) { renderWeightContext(target, window.BIO_AGE_DATA?.weight_context); }
}
