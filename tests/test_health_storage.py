import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import health_storage as store
import run_dashboard_server as server
from api import config as server_config

class StorageTests(unittest.TestCase):
    def payload(self,kind='DEXA'):
        return dict(date='2026-09-22',scan_type=kind,scan_weight=150,home_weight=149,body_fat_pct=30)

    def test_seed_once_and_fresh_process_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);project=root/'project';project.mkdir();disk=root/'disk'
            source=project/'dexa_records.json';source.write_text('[{"date":"2026-01-01","scan_type":"DEXA"}]')
            store.initialize(project,disk)
            rows=store.load_scans(disk/'dexa_records.json')
            store.save_scans(disk/'dexa_records.json',store.merge_scan(rows,self.payload()))
            (disk/'weight_entries.json').write_text('[{"weight_lb":149}]')
            source.write_text('[]') # new checkout must not reset persistent records
            code='import health_storage as s,sys,json;from pathlib import Path;s.initialize(Path(sys.argv[1]),Path(sys.argv[2]));print(len(s.load_scans(Path(sys.argv[2])/"dexa_records.json")))'
            result=subprocess.check_output([sys.executable,'-B','-c',code,str(project),str(disk)],cwd=server.PROJECT_DIR,text=True)
            self.assertEqual(result.strip(),'2')
            self.assertEqual(json.loads((disk/'weight_entries.json').read_text())[0]['weight_lb'],149)
            self.assertEqual(len(list((disk/'record_backups').glob('*.json'))),1)

    def test_modalities_and_existing_fields_preserved(self):
        records=[dict(date='2026-09-22',scan_type='DEXA',height_in=62,location='Original',segmental={'arm':5}),dict(date='2026-01-01',scan_type='InBody',height_in=62)]
        rows=store.merge_scan(records,self.payload('InBody'))
        self.assertEqual(len(rows),3)
        rows=store.merge_scan(rows,self.payload('DEXA'))
        self.assertEqual(len(rows),3)
        dexa=next(r for r in rows if r['scan_type']=='DEXA')
        self.assertEqual(dexa['location'],'Original');self.assertEqual(dexa['segmental'],{'arm':5})
        with self.assertRaises(ValueError): store.merge_scan(rows,{**self.payload(),'scan_type':None})
        with self.assertRaises(ValueError): store.merge_scan(rows,{**self.payload(),'scan_weight':float('nan')})

    def test_corrupt_scan_file_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'dexa_records.json';path.write_text('{broken')
            with self.assertRaises(ValueError): store.save_scans(path,[])
            self.assertEqual(path.read_text(),'{broken')

    def test_hosted_requires_configured_real_mount(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertFalse(store.status(Path('/var/data'),True)['writes_enabled'])
            self.assertTrue(store.status(Path('/local'),False)['writes_enabled'])
            with self.assertRaises(OSError): store.require_durable(Path('/var/data'),True)
        with patch.dict(os.environ,{'HEALTH_DATA_DIR':'/var/data'}),patch.object(store.os.path,'ismount',return_value=False):
            self.assertFalse(store.status(Path('/var/data'),True)['writes_enabled'])
        with patch.dict(os.environ,{'HEALTH_DATA_DIR':'/var/data'}),patch.object(store.os.path,'ismount',side_effect=lambda p:str(p)=='/var/data'):
            self.assertTrue(store.status(Path('/var/data/health'),True)['writes_enabled'])

    def test_api_scan_save_reads_same_file_after_new_handler(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(server_config,'HOSTED_RENDER',False):
            path=Path(directory)/'dexa_records.json'
            with patch.object(server_config,'DEXA_DATA_PATH',path):
                handler=object.__new__(server.DashboardHandler)
                body=json.dumps(self.payload()).encode();handler.headers={'Content-Length':str(len(body))};handler.rfile=io.BytesIO(body)
                responses=[];handler._json=lambda status,data:responses.append((status,data))
                handler._handle_admin_save_dexa_record()
                self.assertEqual(responses[0][0],200)
                fresh=object.__new__(server.DashboardHandler)
                self.assertEqual(fresh._load_dexa_data()[0]['scan_type'],'DEXA')

if __name__=='__main__':unittest.main()
