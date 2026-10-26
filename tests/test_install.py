import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallationTests(unittest.TestCase):
    def run_install(self, *args, env=None):
        return subprocess.run([sys.executable, '-B', str(ROOT / 'install.py'), *map(str, args)],
                              capture_output=True, text=True, env=env)

    def test_dry_run_first_install_repeat_and_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'skills'
            result = self.run_install('--dest', dest, '--dry-run')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(dest.exists())
            result = self.run_install('--dest', dest)
            self.assertEqual(result.returncode, 0, result.stderr)
            for source in (ROOT / 'skills').rglob('*'):
                if source.is_file():
                    self.assertEqual(source.read_bytes(), (dest / source.relative_to(ROOT / 'skills')).read_bytes())
            result = self.run_install('--dest', dest)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(set(json.loads(result.stdout)['skills'].values()), {'unchanged'})

    def test_conflict_preserves_existing_and_does_not_install_other_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            old = dest / 'analyze-workout-video'
            old.mkdir()
            (old / 'SKILL.md').write_text('User customization')
            result = self.run_install('--dest', dest)
            self.assertEqual(result.returncode, 1)
            self.assertFalse((dest / 'home-fitness-coach').exists())
            self.assertEqual((old / 'SKILL.md').read_text(), 'User customization')

    def test_environment_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_install(env={**os.environ, 'CODEX_HOME': tmp})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((Path(tmp) / 'skills/home-fitness-coach/SKILL.md').is_file())

    def test_reject_checkout_destination_and_symlink(self):
        result = self.run_install('--dest', ROOT / 'private-install')
        self.assertEqual(result.returncode, 1)
        self.assertFalse((ROOT / 'private-install').exists())
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            external = dest / 'external'
            external.mkdir()
            (dest / 'home-fitness-coach').symlink_to(external, target_is_directory=True)
            result = self.run_install('--dest', dest)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(list(external.iterdir()), [])

    def test_distribution_rejects_extra_file_and_sensitive_content(self):
        spec = importlib.util.spec_from_file_location('distribution', ROOT / 'scripts/check_distribution.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'release-files.txt').write_text('release-files.txt\nREADME.md\n')
            (root / 'README.md').write_text('Synthetic public material')
            self.assertEqual(module.audit(root), [])
            (root / 'private.json').write_text('{}')
            self.assertTrue(any('not in release inventory' in e for e in module.audit(root)))
            (root / 'private.json').unlink()
            (root / 'README.md').write_text('/' + 'Users' + '/' + 'synthetic-user' + '/record')
            self.assertTrue(any('absolute home path' in e for e in module.audit(root)))


if __name__ == '__main__':
    unittest.main()
