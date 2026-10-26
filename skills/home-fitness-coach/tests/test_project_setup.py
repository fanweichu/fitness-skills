import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from init_project import initialize


class ProjectSetupTests(unittest.TestCase):
    def test_fresh_project_plan_context_and_no_overwrite(self):
        with tempfile.TemporaryDirectory(prefix='coach-portable-') as tmp:
            root=initialize(Path(tmp)/'project')
            self.assertEqual(json.loads((root/'data/coach-profile.json').read_text()),{})
            context=subprocess.run([sys.executable,str(root/'scripts/fitness_data.py'),'--project',str(root),'context'],capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(context.stdout)['recent_actuals'],[])
            subprocess.run([sys.executable,str(root/'scripts/fitness_data.py'),'--project',str(root),'rebuild'],capture_output=True,text=True,check=True)
            self.assertEqual(json.loads((root/'derived/coach-state.json').read_text())['latest_date'],None)
            self.assertTrue((root/'dashboard.html').exists())
            self.assertFalse((root/'exercise-guides.html').exists())
            with self.assertRaises(ValueError):initialize(root)
            plan=root/'input.json';plan.write_text(json.dumps({'workout_id':'2000-01-01','date':'2000-01-01','plan':{'blocks':[]}}))
            subprocess.run([sys.executable,str(root/'scripts/training_pages.py'),'--project',str(root),'save-workout','--input',str(plan)],capture_output=True,check=True)
            self.assertTrue((root/'workouts/2000-01-01.html').exists())
            self.assertEqual(list((root/'data/sessions').glob('*.json')),[])

if __name__=='__main__':unittest.main()
