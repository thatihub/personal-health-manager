"""Durable location and guarded scan writes; never silently replace unreadable data."""
import json
import math
import os
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path


def status(data_dir, hosted=False):
    configured = bool(os.getenv('HEALTH_DATA_DIR'))
    mounted = any(os.path.ismount(p) for p in [data_dir, *data_dir.parents] if p != Path('/'))
    writable = not hosted or (configured and mounted)
    return {'writes_enabled': writable, 'mode': 'persistent_disk' if hosted and writable else ('unconfigured' if hosted else 'local_files'),
            'message': 'Records are saved on the configured persistent disk.' if hosted and writable else ('Records are saved in local files.' if not hosted else 'Saving is disabled: attach a Render persistent disk and set HEALTH_DATA_DIR to its mount path. Temporary server files are lost on restart.')}


def require_durable(data_dir, hosted=False):
    result=status(data_dir,hosted)
    if not result['writes_enabled']:
        raise OSError(result['message'])


def initialize(project_dir, data_dir):
    """Seed once; deployed source must never overwrite the durable record set."""
    if data_dir == project_dir:
        return
    data_dir.mkdir(parents=True,exist_ok=True)
    for name in ('dexa_records.json','weight_entries.json'):
        source, target = project_dir/name, data_dir/name
        if target.exists() or not source.exists():
            continue
        # Validate before seeding, and install a complete file atomically.
        json.loads(source.read_text(encoding='utf-8'))
        with tempfile.NamedTemporaryFile(dir=data_dir,delete=False) as file:
            temporary=Path(file.name)
        try:
            shutil.copyfile(source,temporary)
            try: os.link(temporary,target)  # refuses to replace a concurrent seed
            except FileExistsError: pass
        finally: temporary.unlink(missing_ok=True)


def load_scans(path):
    if not path.exists():
        return []
    rows=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(rows,list) or any(not isinstance(row,dict) or not row.get('date') for row in rows):
        raise ValueError('Invalid scan file. Existing data was preserved; restore a valid backup before saving.')
    return rows


def save_scans(path, rows):
    """Caller serializes updates. Keep the previous file for recovery."""
    if path.exists():
        load_scans(path)
        backup_dir=path.parent/'record_backups';backup_dir.mkdir(exist_ok=True)
        stamp=datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        shutil.copy2(path,backup_dir/f'dexa_records-{stamp}.json')
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as file:
        temporary=Path(file.name)
        json.dump(rows,file,indent=2,allow_nan=False)
    try: temporary.replace(path)
    finally: temporary.unlink(missing_ok=True)


def merge_scan(records,payload):
    if not isinstance(payload,dict): raise ValueError('Expected a scan record.')
    collected=date.fromisoformat(str(payload.get('date',''))).isoformat()
    kind=payload.get('scan_type')
    if kind not in ('DEXA','InBody'): raise ValueError('Select DEXA or InBody as the scan type.')
    def number(key,required=False,positive=False):
        value=payload.get(key)
        if value is None or value=='':
            if required: raise ValueError(f'{key} is required.')
            return None
        if isinstance(value,bool): raise ValueError(f'Invalid {key}.')
        value=float(value)
        if not math.isfinite(value) or (positive and value<=0): raise ValueError(f'Invalid {key}.')
        return value
    scan=number('scan_weight',True,True);home=number('home_weight',False,True)
    fat=number('body_fat_pct',True)
    if not 0<=fat<=100: raise ValueError('Body fat must be between 0 and 100%.')
    # Different modalities on the same day are separate observations. Preserve
    # unknown legacy modality rather than silently overwriting or guessing it.
    previous=next((r for r in records if r.get('date')==collected and r.get('scan_type')==kind),{})
    height=previous.get('height_in') or next((r['height_in'] for r in reversed(records) if r.get('height_in')),None)
    basis=home if home is not None else scan
    entry={**previous,'date':collected,'scan_type':kind,'height_in':height,'scan_weight_lb':scan,'home_weight_lb':home,
           'clothing_diff_lb':round(scan-home,2) if home is not None else None,
           'bmi':round(basis/height**2*703,1) if height else None,'body_fat_pct':fat,
           'body_fat_mass_lb':round(scan*fat/100,1),'lean_body_mass_lb':round(scan*(1-fat/100),1),
           'adjusted_body_fat_mass_lb':round(home*fat/100,1) if home is not None else None,
           'adjusted_lean_body_mass_lb':round(home*(1-fat/100),1) if home is not None else None}
    mappings={'muscle_mass':'skeletal_muscle_mass_lb','water':'total_body_water_lb','bmr':'basal_metabolic_rate_kcal','waist':'waist_size_in'}
    for key in ('visceral_fat_area_cm2','visceral_fat_mass_g','visceral_fat_volume_cm3','visceral_fat_level','inbody_score','appendicular_lean_mass_index','lean_height2_index','lean_index_percentile','bmd_g_cm2','bmd_t_score','bmd_z_score'): mappings[key]=key
    for key,target in mappings.items():
        if key in payload: entry[target]=number(key)
    if 'notes' in payload: entry['notes']=str(payload['notes'])
    return sorted([r for r in records if not (r.get('date')==collected and r.get('scan_type')==kind)]+[entry],key=lambda r:r['date'])
