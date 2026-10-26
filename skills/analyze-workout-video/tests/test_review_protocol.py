"""Synthetic module checks; no user footage or actual training records."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import test_count_evidence as fixture
from count_evidence import validate_evidence, validate_report_evidence


class ReviewProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.CountEvidenceTests.setUpClass()
        cls.root=fixture.CountEvidenceTests.root
        cls.receipt_path=fixture.CountEvidenceTests.receipt_path
        cls.receipt=fixture.CountEvidenceTests.receipt
    @classmethod
    def tearDownClass(cls):fixture.CountEvidenceTests.tearDownClass()
    def setUp(self):
        self.work=Path(tempfile.mkdtemp(dir=self.root));self.ep=self.work/'evidence.json'
    def evidence(self):
        e=fixture.CountEvidenceTests.evidence(self);e['schema_version']=3
        e['classification_audit']={'full_context_reviewed':True,'intent':'working_set',
            'closest_lookalike':'fixture adjustment','discriminator':'stable fixture cycles',
            'stable_configuration':True,'consistent_cycle_path':True,'setup_or_adjustment_observed':False}
        e['review'].update(count_context='source_review',expected_totals_exposed=True,
            boundary_pass={'locked_before_counting':True,'segments':copy.deepcopy(e['segments']),'observation':'Synthetic fixture boundaries'},
            reverse_events=[{'event_id':event['id'],'frames':event['frames'][:],
                             'observation':'Synthetic reverse test, not a claimed perceptual review'} for event in reversed(e['events'])])
        return e
    def validate(self,e):return validate_evidence(fixture.write(self.ep,e))
    def test_valid_source_review_and_context_declaration(self):
        self.assertEqual(self.validate(self.evidence())['completed'],3)
        e=self.evidence();e['review']['count_context']='blind_source_review'
        with self.assertRaisesRegex(ValueError,'blind claim'):self.validate(e)
        e['review'].update(expected_totals_exposed=False,isolation_basis='Synthetic isolated fixture')
        self.assertEqual(self.validate(e)['evidence_version'],3)
    def test_reverse_missing_duplicate_order_and_phase_fail(self):
        for change in ('missing','duplicate','order','phase'):
            with self.subTest(change=change):
                e=self.evidence();events=e['review']['reverse_events']
                if change=='missing':events.pop()
                if change=='duplicate':events[1]=copy.deepcopy(events[0])
                if change=='order':events.reverse()
                if change=='phase':events[0]['frames']=e['events'][0]['frames']
                with self.assertRaises(ValueError):self.validate(e)
    def test_boundary_lock_and_segments_required(self):
        for change in ('lock','segments'):
            e=self.evidence()
            if change=='lock':e['review']['boundary_pass']['locked_before_counting']=False
            else:e['review']['boundary_pass']['segments'][0]['end_s']=6
            with self.assertRaisesRegex(ValueError,'boundary pass'):self.validate(e)
    def test_schema5_cannot_use_old_evidence(self):
        helper=fixture.CountEvidenceTests(methodName='test_strict_report_cli')
        helper.work=self.work;helper.ep=self.ep
        report=helper.report();report['schema_version']=5
        with self.assertRaisesRegex(ValueError,'v3'):validate_report_evidence(report,self.work/'report.json')
    def test_window_geometry_and_portrait_pagination(self):
        scripts=fixture.SCRIPTS;source=self.work/'portrait.mp4';window=self.work/'window.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=180x320:rate=10',
            '-t','3','-c:v','libx264','-threads','1',str(source)],check=True,timeout=20)
        subprocess.run([sys.executable,str(scripts/'workout_video_decode.py'),'window',str(source),
            '--start','0','--end','3','--width','180','--decoder','software','--output',str(window)],
            cwd=self.work,check=True,capture_output=True,timeout=30)
        marker=json.loads(window.with_suffix('.complete.json').read_text())
        self.assertEqual(marker['display_geometry']['rotation'],0)
        self.assertEqual((marker['validation']['width'],marker['validation']['height']),(180,320))
        subprocess.run([sys.executable,str(scripts/'review_runtime.py'),'extract',str(window),str(self.work/'portrait-frames'),
            '--file','synthetic','--start','0','--end','2.9','--step','.1'],check=True,capture_output=True,timeout=30)
        receipt=json.loads((self.work/'portrait-frames/receipt.json').read_text())
        self.assertEqual(len(receipt['frames']),30)
        self.assertTrue(all(p['height']<=2000 for p in receipt['pages']))
        self.assertFalse((self.work/'manifest.json').exists(), 'extraction is not a viewing claim')
        # A decodable but mismatched cache must be recreated, not returned forever.
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=160x90:rate=10',
            '-t','3','-c:v','libx264','-threads','1',str(window)],check=True,timeout=20)
        subprocess.run([sys.executable,str(scripts/'workout_video_decode.py'),'window',str(source),
            '--start','0','--end','3','--width','180','--decoder','software','--output',str(window)],
            cwd=self.work,check=True,capture_output=True,timeout=30)
        from window_geometry import geometry
        repaired,_=geometry(window)
        self.assertEqual((repaired['width'],repaired['height']),(180,320))

if __name__=='__main__':unittest.main()
