import math
import unittest
from datetime import date
from unittest.mock import patch
import phenoage as p
import run_dashboard_server as server
from api import config as server_config


class PhenoAgeTests(unittest.TestCase):
    def rows(self, collected='2026-09-15', source='Test lab'):
        values = [45, 80, 5, .1, 30, 90, 13, 70, 6]
        return [dict(standard_test_name=marker, value_numeric=value, value_text=str(value),
                     original_unit=spec[1], standard_unit=spec[1], collection_date=collected,
                     lab_source=source, original_test_name=marker)
                for (marker, spec), value in zip(p.SPECS.items(), values)]

    def assess(self, rows=None, dob=date(1976,9,15)):
        return p.assess(self.rows() if rows is None else rows, dob, today=date(2026,9,22))

    def test_published_equation_reference(self):
        result=self.assess()
        self.assertEqual(result['status'],'available')
        values={row['marker']:row['value'] for row in result['inputs']}
        # Fixed independently evaluated direct Gompertz equation, not the
        # algebraically simplified calculation used by the implementation.
        self.assertAlmostEqual(p.calculate(values,50),41.838108630942244,places=10)
        x=-19.90667-.03359355*45+.009506491*80+.1953192*5+.09536762*math.log(.1)-.01199984*30+.02676401*90+.3306156*13+.001868778*70+.05542406*6+.08035356*50
        risk=1-math.exp(-1.51714*math.exp(x)/.007692696)
        reference=141.50225+math.log(-.0055305*math.log(1-risk))/.090165
        self.assertAlmostEqual(p.calculate(values,50),reference,places=10)
        self.assertEqual(result['age_at_collection'],50)
        self.assertEqual(result['age_difference'],round(reference-50,1))

    def test_conventional_units_and_legacy_normalization(self):
        rows=self.rows()
        for row in rows:
            marker=row['standard_test_name']
            if marker=='albumin': row.update(value_text='4.5',original_unit='g/dL')
            if marker=='creatinine': row.update(value_text=str(80/88.4),original_unit='mg/dL')
            if marker=='glucose_fasting': row.update(value_text='90',original_unit='mg/dL')
            if marker=='crp': row.update(value_text='1',original_unit='mg/L',standard_test_name='hs_crp')
            if marker=='wbc': row.update(value_text='6000',original_unit='cells/µL')
            # Incorrect legacy normalized values must never replace raw units/text.
            row['value_numeric']=999
        self.assertEqual(self.assess(rows)['estimated_age'],self.assess()['estimated_age'])

    def test_no_cross_date_or_source_assembly(self):
        rows=self.rows();rows[-1]['collection_date']='2026-09-14'
        self.assertIsNone(self.assess(rows)['estimated_age'])
        rows=self.rows();rows[-1]['lab_source']='Another lab'
        self.assertIsNone(self.assess(rows)['estimated_age'])
        rows=self.rows()+self.rows('2026-09-20')[:1]
        result=self.assess(rows)
        self.assertEqual(result['collection_date'],'2026-09-20')
        self.assertIsNone(result['estimated_age'])
        self.assertEqual(len(result['history']),1)
        self.assertEqual(self.assess(self.rows()+self.rows('2099-01-01'))['collection_date'],'2026-09-15')

    def test_unknown_age_birthdays_and_adult_limit(self):
        self.assertIsNone(self.assess(dob=None)['estimated_age'])
        self.assertIsNone(self.assess(dob=date(2010,1,1))['estimated_age'])
        self.assertEqual(p.age_on(date(1976,9,16),date(2026,9,15)),49)
        self.assertEqual(p.age_on(date(1976,9,15),date(2026,9,15)),50)
        self.assertIsNone(self.assess([])['estimated_age'])

    def test_invalid_units_censoring_and_conflicts(self):
        for marker,changes in [('crp',dict(value_text='0')),('crp',dict(value_text='<0.1')),
                ('albumin',dict(original_unit='')),('albumin',dict(original_unit='mg/mL')),
                ('wbc',dict(value_text='NaN')),('wbc',dict(value_text='1e999')),
                ('lymphocyte_pct',dict(value_text='101')),('lymphocyte_pct',dict(original_test_name='Absolute lymphocytes')),
                ('rdw',dict(original_test_name='RDW-SD')),('mcv',dict(value_text='-5'))]:
            with self.subTest(marker=marker,changes=changes):
                rows=self.rows();next(row for row in rows if row['standard_test_name']==marker).update(changes)
                self.assertIsNone(self.assess(rows)['estimated_age'])
        rows=self.rows();rows.append({**rows[0],'value_text':'40'})
        self.assertIn('Albumin',self.assess(rows)['invalid'])
        rows=self.rows();rows.append(dict(rows[0]))
        self.assertEqual(self.assess(rows)['status'],'available')

    def test_server_preserves_custom_scores_separately(self):
        handler=object.__new__(server.DashboardHandler)
        with patch.object(handler,'_normalized_results_from_consolidated',return_value=self.rows()), patch.object(handler,'_load_ag_imports',return_value={}), patch.object(handler,'_date_of_birth',return_value=date(1976,9,15)), patch.object(handler,'_weight_context',return_value={'weight_lb':150}):
            output=handler._compute_ag_snapshot()
            self.assertEqual(output['model_version'],p.MODEL_VERSION)
            self.assertEqual(output['estimated_bio_age'],self.assess()['estimated_age'])
            self.assertTrue(output['section_scores'])
            with patch.object(handler,'_weight_context',return_value={'weight_lb':140}):
                updated=handler._compute_ag_snapshot()
            self.assertEqual(output['estimated_bio_age'],updated['estimated_bio_age'])
            self.assertEqual(updated['weight_context']['weight_lb'],140)
        with patch.object(handler,'_normalized_results_from_consolidated',return_value=[]), patch.object(handler,'_load_ag_imports',return_value={}), patch.object(handler,'_date_of_birth',return_value=None), patch.object(handler,'_weight_context',return_value={}):
            self.assertIsNone(handler._compute_ag_snapshot()['estimated_bio_age'])

    def test_import_normalization_keeps_formula_units_correct(self):
        handler=object.__new__(server.DashboardHandler)
        names=['Albumin','Creatinine','Glucose','C-reactive protein','Lymphocyte %','Mean corpuscular volume','Red cell distribution width','Alkaline phosphatase','White blood cell count']
        normalized=[]
        for row,name in zip(self.rows(),names):
            converted=handler._normalize_import_row({'test':name,'value':row['value_text'],'unit':row['original_unit']},'2026-09-15','Test lab','text','test')
            normalized.append(converted)
        self.assertEqual(self.assess(normalized)['estimated_age'],self.assess()['estimated_age'])
        self.assertEqual(normalized[0]['standard_unit'],'g/dL')
        self.assertEqual(normalized[0]['value_numeric'],4.5)
        self.assertEqual(normalized[3]['standard_unit'],'mg/L')
        self.assertEqual(normalized[3]['value_numeric'],1)
        self.assertEqual(handler._convert_to_standard_unit('albumin',45,'mystery')[1],'mystery')

    def test_vault_missing_dob_has_no_default(self):
        handler=object.__new__(server.DashboardHandler)
        with patch.object(server_config,'_decode_env_text',return_value='{}'):
            self.assertIsNone(handler._chronological_age())

if __name__=='__main__': unittest.main()
