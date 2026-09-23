"""Daily local-time measurements; scan observations remain immutable."""
import json
import math
import re
from datetime import datetime
from uuid import uuid4


def load_entries(path):
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(rows, list):
        raise ValueError('Weight file must contain a list; existing file was preserved.')
    dates = set()
    ids = set()
    for row in rows:
        validate(row)
        if not isinstance(row.get('id'), str) or not row['id'] or not row.get('created_at') or row['id'] in ids or row['measured_at'][:10] in dates:
            raise ValueError('Invalid or duplicate stored weight entries; file was preserved.')
        dates.add(row['measured_at'][:10])
        ids.add(row['id'])
    return rows


def validate(payload):
    if not isinstance(payload, dict):
        raise ValueError('Expected a weight entry.')
    stamp = payload.get('measured_at', '')
    if not isinstance(stamp, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}', stamp):
        raise ValueError('Enter a local date and time.')
    datetime.fromisoformat(stamp)
    weight = payload.get('weight_lb')
    if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight <= 0:
        raise ValueError('Weight must be a positive finite number in pounds.')
    return stamp, float(weight)


def save_entry(rows, payload):
    stamp, weight = validate(payload)
    entry_id = payload.get('id')
    existing = next((r for r in rows if r['id'] == entry_id), None)
    if entry_id and existing is None:
        raise LookupError('Entry no longer exists. Reload before editing.')
    if any(r['measured_at'][:10] == stamp[:10] and r['id'] != entry_id for r in rows):
        raise FileExistsError('An entry already exists for this day. Edit that entry instead.')
    now = datetime.now().astimezone().isoformat()
    entry = {**(existing or {}), 'id': entry_id or uuid4().hex, 'measured_at': stamp,
             'weight_lb': weight, 'created_at': existing['created_at'] if existing else now, 'updated_at': now}
    return sorted([r for r in rows if r['id'] != entry_id] + [entry], key=lambda r: r['measured_at'])


def health_context(rows, scans, today=None, reference=None):
    today = today or datetime.now().date().isoformat()
    eligible = sorted([r for r in rows if r['measured_at'][:10] <= today], key=lambda r: r['measured_at'])
    scans = sorted([s for s in scans if s.get('date', '') <= today], key=lambda s: s.get('date', ''))
    latest = eligible[-1] if eligible else None
    if latest:
        weight, date, source = latest['weight_lb'], latest['measured_at'][:10], 'Daily weight'
    elif reference and reference.get('date', '') <= today:
        weight, date, source = reference['weight_lb'], reference['date'], reference['source']
    elif scans:
        scan = scans[-1]
        weight, date, source = scan.get('home_weight_lb') or scan.get('scan_weight_lb'), scan['date'], 'Scan record'
    else:
        weight = date = source = None
    height_scan = next((s for s in reversed(scans) if isinstance(s.get('height_in'), (int, float)) and s['height_in'] > 0), None)
    height = height_scan['height_in'] if height_scan else None
    return {'weight_lb': weight, 'date': date, 'source': source,
            'height_in': height, 'height_date': height_scan['date'] if height_scan else None,
            'bmi': round(weight / height ** 2 * 703, 1) if weight and height else None,
            'change_lb': round(eligible[-1]['weight_lb'] - eligible[0]['weight_lb'], 2) if len(eligible) > 1 else None,
            'change_since': eligible[0]['measured_at'][:10] if len(eligible) > 1 else None,
            'latest_scans': [next(s for s in reversed(scans) if s.get('scan_type') == kind) for kind in sorted({s.get('scan_type', '') for s in scans})],
            'age_model_note': 'Daily weight updates BMI and weight trends. DEXA/InBody measurements provide dated body-composition context. Neither changes the clinical PhenoAge formula, which requires nine blood markers and age. Scan composition and BMR remain measurements from the scan date.'}
