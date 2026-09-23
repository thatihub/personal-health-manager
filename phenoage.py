"""Clinical PhenoAge (Levine 2018), not DNAm PhenoAge.

Equations: https://doi.org/10.18632/aging.101414, Supplement 1.
Full-precision coefficients: BioAge R/phenoage_calc.R, orig=TRUE:
https://github.com/dayoonkwon/BioAge/blob/master/R/phenoage_calc.R
No coefficients are trained or adjusted here. See README for selection policy.
"""
import math
import re
from datetime import date

MODEL_VERSION = 'clinical-phenoage-levine2018-v1'
SOURCE_URL = 'https://www.aging-us.com/article/101414/text'
# marker: (display name, formula unit, coefficient, accepted units -> multiplier)
SPECS = {
    'albumin': ('Albumin', 'g/L', -0.03359355, {'g/l': 1, 'g/dl': 10}),
    'creatinine': ('Creatinine', 'µmol/L', 0.009506491, {'umol/l': 1, 'mg/dl': 88.4}),
    'glucose_fasting': ('Glucose', 'mmol/L', 0.1953192, {'mmol/l': 1, 'mg/dl': 1/18}),
    'crp': ('CRP / hs-CRP', 'mg/dL', 0.09536762, {'mg/dl': 1, 'mg/l': 0.1}),
    'lymphocyte_pct': ('Lymphocytes (%)', '%', -0.01199984, {'%': 1}),
    'mcv': ('MCV', 'fL', 0.02676401, {'fl': 1}),
    'rdw': ('RDW-CV (%)', '%', 0.3306156, {'%': 1}),
    'alkaline_phosphatase': ('Alkaline phosphatase', 'U/L', 0.001868778, {'u/l': 1, 'iu/l': 1}),
    'wbc': ('White blood cells', '10³/µL', 0.05542406, {'10^3/ul': 1, '10³/ul': 1, 'k/ul': 1, '10^9/l': 1, '10⁹/l': 1, 'cells/ul': 0.001}),
}


def age_on(dob, collected):
    if dob is None or collected < dob:
        return None
    return collected.year - dob.year - ((collected.month, collected.day) < (dob.month, dob.day))


def marker_for(row):
    marker = row.get('standard_test_name')
    return 'crp' if marker == 'hs_crp' else marker


def converted_value(row, marker):
    # Imported legacy normalization may have mislabeled SI values. Prefer the
    # original numeric text AND original units, together, when they are present.
    raw = row.get('value_text', row.get('value_numeric'))
    if isinstance(raw, bool) or raw is None or not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', str(raw).strip()):
        raise ValueError('An exact numeric result is required; limits such as < or > cannot be substituted.')
    value = float(raw)
    unit = str(row.get('original_unit', row.get('standard_unit', ''))).strip().lower().replace('μ', 'u').replace('µ', 'u').replace(' ', '')
    factors = SPECS[marker][3]
    if unit not in factors:
        raise ValueError('Missing or unsupported units; review the original report.')
    value *= factors[unit]
    if not math.isfinite(value) or value < 0 or (marker != 'lymphocyte_pct' and value == 0):
        raise ValueError('A finite positive result is required (lymphocytes may be zero).')
    if marker == 'lymphocyte_pct' and value > 100:
        raise ValueError('Lymphocyte percentage must be between 0 and 100.')
    name = str(row.get('original_test_name', '')).lower()
    if marker == 'lymphocyte_pct' and ('absolute' in name or re.search(r'\babs\b', name)):
        raise ValueError('Use lymphocyte percentage, not an absolute count.')
    if marker == 'rdw' and re.search(r'rdw[ _-]*sd', name):
        raise ValueError('Use RDW-CV (%), not RDW-SD.')
    return value


def calculate(values, age):
    """Inputs must already be validated and expressed in the formula units."""
    if age is None or age < 20 or not math.isfinite(age):
        raise ValueError('The published model requires adult age (20+) at collection.')
    xb = -19.90667 + 0.08035356 * age
    for marker, (_, _, coefficient, _) in SPECS.items():
        value = values[marker]
        if not math.isfinite(value) or value < 0 or (marker != 'lymphocyte_pct' and value == 0):
            raise ValueError('Invalid formula input.')
        xb += coefficient * (math.log(value) if marker == 'crp' else value)
    # Algebraically equivalent to BioAge orig=TRUE, avoiding cancellation in
    # log(1 - mortality_score) when the mortality probability rounds to 0 or 1.
    result = 141.50225 + (math.log(0.0055305 * 1.51714 / 0.007692696) + xb) / 0.090165
    if not math.isfinite(result):
        raise ValueError('Formula result is not finite.')
    return result


def assess_panel(rows, collected, source, dob):
    inputs, missing, invalid, values = [], [], [], {}
    for marker, (label, unit, _, _) in SPECS.items():
        candidates = [row for row in rows if marker_for(row) == marker]
        item = {'marker': marker, 'label': label, 'unit': unit, 'value': None, 'status': 'missing', 'detail': 'Not recorded for this collection date and lab source.'}
        if not candidates:
            missing.append(label)
        else:
            try:
                converted = [converted_value(row, marker) for row in candidates]
                if any(not math.isclose(value, converted[0], rel_tol=1e-6, abs_tol=1e-8) for value in converted[1:]):
                    raise ValueError('Conflicting same-day values; review the source records.')
                values[marker] = converted[0]
                item.update(value=converted[0], status='ready', detail='Units checked against the original result.')
            except (ValueError, TypeError, OverflowError) as error:
                invalid.append(label)
                item.update(status='review', detail=str(error))
        inputs.append(item)
    age = age_on(dob, collected) if collected else None
    reasons = []
    if age is None:
        reasons.append('A valid date of birth is required; no default age is assumed.')
    elif age < 20:
        reasons.append('This model was developed for adults aged 20 and over.')
    if missing:
        reasons.append('Missing: ' + ', '.join(missing) + '.')
    if invalid:
        reasons.append('Review results or units: ' + ', '.join(invalid) + '.')
    estimate = None
    if not reasons:
        try:
            estimate = calculate(values, age)
        except (ValueError, OverflowError) as error:
            reasons.append(str(error))
    return {'status': 'available' if estimate is not None else 'unavailable',
            'collection_date': collected.isoformat() if collected else None, 'lab_source': source,
            'age_at_collection': age, 'estimated_age': round(estimate, 1) if estimate is not None else None,
            'age_difference': round(estimate - age, 1) if estimate is not None else None,
            'ready_count': len(values), 'required_count': len(SPECS), 'inputs': inputs,
            'missing': missing, 'invalid': invalid, 'reasons': reasons}


def assess(rows, dob, today=None):
    today = today or date.today()
    groups = {}
    for row in rows:
        if marker_for(row) not in SPECS:
            continue
        try:
            collected = date.fromisoformat(str(row.get('collection_date', '')))
        except ValueError:
            continue
        if collected > today:
            continue
        source = str(row.get('lab_source') or 'Unknown')
        groups.setdefault((collected, source), []).append(row)
    panels = [assess_panel(group, collected, source, dob) for (collected, source), group in groups.items()]
    # Latest relevant draw, then most complete source on that date. Do not
    # silently replace an incomplete latest assessment with an older estimate.
    panels.sort(key=lambda panel: (panel['collection_date'], panel['ready_count'], panel['lab_source']), reverse=True)
    latest = panels[0] if panels else assess_panel([], None, None, dob)
    return {**latest, 'model_version': MODEL_VERSION, 'source_url': SOURCE_URL,
            'history': [{key: p[key] for key in ('collection_date', 'lab_source', 'age_at_collection', 'estimated_age', 'age_difference')} for p in panels if p['status'] == 'available'],
            'selection_policy': 'All nine markers must come from the same collection date and lab source. No imputation or mixing across dates. Latest relevant collection is shown.',
            'interpretation': 'Clinical PhenoAge is a population-derived risk-equivalent age, not a diagnosis, lifespan prediction, or DNA-methylation test. The age difference is a simple subtraction, not regression-adjusted PhenoAgeAccel.'}
