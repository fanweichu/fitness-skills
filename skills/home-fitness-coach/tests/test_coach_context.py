"""Targeted read-only context and correction overlay checks using synthetic data."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import training_pages as pages
from coach_context import compact_context


class CoachContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.plan={'workout_id':'2000-01-01','date':'2000-01-01','plan':{'summary':'Synthetic plan','blocks':[]}}
        pages.save_workout(self.root,self.plan)
        self.correction={'id':'support-correction','date':'2000-01-02','scope':'support movement',
                         'superseded':'Movement alone proves instability',
                         'replacement':'Judge the observed effect on balance',
                         'basis':'Source review confirms maintained contact and control'}

    def tearDown(self):self.tmp.cleanup()
    def hashes(self):return {str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in self.root.rglob('*') if p.is_file()}

    def test_context_is_read_only_and_review_has_no_prescription(self):
        before=self.hashes()
        ctx=compact_context(self.root)
        self.assertEqual(ctx['latest_prescription']['workout_id'],'2000-01-01')
        self.assertNotIn('plan',ctx)
        review=compact_context(self.root,mode='review')
        self.assertNotIn('latest_prescription',review)
        self.assertNotIn('recent_actuals',review)
        self.assertEqual(before,self.hashes())

    def test_correction_preserves_plan_revision_and_reaches_page_context(self):
        old=pages.read(self.root/'data/workouts/2000-01-01.json')
        new=pages.save_workout(self.root,{'workout_id':'2000-01-01','date':'2000-01-01','corrections':[self.correction]})
        self.assertEqual(new['plan'],old['plan']);self.assertEqual(new['revision'],2)
        self.assertEqual(pages.read(self.root/'data/revisions/workouts/2000-01-01-r001.json'),old)
        self.assertEqual(compact_context(self.root,last=1)['corrections'][0]['replacement'],self.correction['replacement'])
        html=pages.workout_html(pages.collect(self.root)[0])
        self.assertIn('id="corrections"',html);self.assertIn('<s>'+self.correction['superseded']+'</s>',html)
        self.assertIn(self.correction['replacement'],html)

    def test_invalid_correction_does_not_mutate_any_file(self):
        before=self.hashes();bad=copy.deepcopy(self.correction);bad.pop('basis')
        with self.assertRaises(ValueError):pages.save_workout(self.root,{**self.plan,'corrections':[bad]})
        self.assertEqual(before,self.hashes())
        with self.assertRaises(ValueError):pages.save_workout(self.root,{**self.plan,'corrections':[self.correction,self.correction]})
        self.assertEqual(before,self.hashes())

    def test_focus_and_actual_load_are_in_context_without_plan_as_actual(self):
        pages.write(self.root/'data/sessions/2000-01-01-001.json',{
            'session_id':'2000-01-01-001','date':'2000-01-01','exercises':[
                {'exercise_id':'row.fixture','name':'Row','sets':[{'completed_reps':7,'side':'bilateral','load':{'value':4},'rir':None}]}],
            'technique_focuses':[{'title':'Control','cue':'Slow the descent','exercise_id':'row.fixture'}],
            'user_feedback':{'notes':'Reported effort applies to this session'}})
        c=compact_context(self.root);actual=c['recent_actuals'][0]
        self.assertEqual(actual['exercises'][0]['sets'][0]['load'],{'value':4})
        self.assertEqual(actual['technique_focuses'][0]['cue'],'Slow the descent')
        self.assertEqual(len(actual['feedback']),1)
        self.assertLess(len(json.dumps(c)),5000)

if __name__=='__main__':unittest.main()
