import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import weight_tracking as w
import run_dashboard_server as server
from api import config as server_config


class WeightTests(unittest.TestCase):
    def entry(self, stamp='2026-09-22T23:45', weight=150, **extra):
        return dict(measured_at=stamp, weight_lb=weight, **extra)

    def test_edit_and_duplicate_date(self):
        rows=w.save_entry([], self.entry())
        with self.assertRaises(FileExistsError): w.save_entry(rows,self.entry('2026-09-22T01:00'))
        edited=w.save_entry(rows,self.entry('2026-09-21T02:30',149,id=rows[0]['id']))
        self.assertEqual(edited[0]['id'],rows[0]['id'])
        self.assertEqual(edited[0]['created_at'],rows[0]['created_at'])
        rows=w.save_entry(edited,self.entry())
        with self.assertRaises(FileExistsError): w.save_entry(rows,self.entry(id=edited[0]['id']))
        with self.assertRaises(LookupError): w.save_entry(rows,self.entry(id='missing'))

    def test_validation(self):
        for value in [0,-1,float('nan'),float('inf'),True,'150',None]:
            with self.subTest(value=value), self.assertRaises(ValueError): w.save_entry([],self.entry(weight=value))
        for stamp in ['2026-02-30T12:00','2026-09-22','2026-09-22T12:00Z']:
            with self.assertRaises(ValueError): w.save_entry([],self.entry(stamp))

    def test_context_and_preserved_scans(self):
        scans=[dict(date='2026-08-01',scan_type='DEXA',height_in=62,scan_weight_lb=160,body_fat_pct=30,basal_metabolic_rate_kcal=1200)]
        original=json.dumps(scans)
        rows=w.save_entry([],self.entry('2026-09-20T08:00',155))
        rows=w.save_entry(rows,self.entry('2026-09-21T08:00',150))
        rows=w.save_entry(rows,self.entry('2099-01-01T08:00',90))
        c=w.health_context(rows,scans,today='2026-09-22')
        self.assertEqual(c['weight_lb'],150)
        self.assertEqual(c['bmi'],27.4)
        self.assertEqual(c['change_lb'],-5)
        self.assertEqual(json.dumps(scans),original)
        self.assertEqual(w.health_context([],scans)['weight_lb'],160)
        self.assertIsNone(w.health_context([],[])['bmi'])
        self.assertEqual(w.health_context([],scans,reference=dict(date='2026-08-29',weight_lb=158,source='legacy'))['weight_lb'],158)

    def test_api_persistence_conflicts_corrupt_file_and_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'weight_entries.json'
            with patch.object(server_config,'WEIGHT_DATA_PATH',path), patch.object(server_config,'ADMIN_AUTH_USER',''), patch.object(server_config,'ADMIN_AUTH_PASS',''):
                http=ThreadingHTTPServer(('127.0.0.1',0),server.DashboardHandler)
                thread=Thread(target=http.serve_forever,daemon=True);thread.start()
                base=f'http://127.0.0.1:{http.server_port}'
                def post(payload):
                    return urlopen(Request(base+'/api/admin/weight-entry',json.dumps(payload).encode(),{'Content-Type':'application/json'}))
                try:
                    with post(self.entry()) as response: self.assertEqual(response.status,200)
                    persisted=w.load_entries(path)
                    self.assertEqual(persisted[0]['measured_at'],'2026-09-22T23:45')
                    with self.assertRaises(HTTPError) as caught: post(self.entry())
                    self.assertEqual(caught.exception.code,409)
                    with post(self.entry('2026-09-21T03:00',148,id=persisted[0]['id'])): pass
                    with urlopen(base+'/api/weight-data') as response:
                        data=json.load(response)
                    self.assertEqual(len(data['rows']),1)
                    self.assertEqual(data['health_context']['weight_lb'],148)
                    with patch.object(server_config,'ADMIN_AUTH_USER','tester'),patch.object(server_config,'ADMIN_AUTH_PASS','secret'):
                        with self.assertRaises(HTTPError) as caught: post(self.entry())
                        self.assertEqual(caught.exception.code,401)
                    path.write_text('{broken')
                    with self.assertRaises(HTTPError): post(self.entry())
                    self.assertEqual(path.read_text(),'{broken')
                finally:
                    http.shutdown();http.server_close();thread.join()

if __name__=='__main__': unittest.main()
