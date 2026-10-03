"""Closed code-binding probes: optimization cannot bypass source pin checks."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest

ENTRY = Path(__file__).resolve().parents[1] / 'scripts/repo_bound_profit_entry.py'
SKILL = 'domains/data_operations/skills/manage-profit-settlement/scripts/'
NAMES = {'report':'profit_report.py', 'tiktok-monthly':'build_tiktok_monthly_from_evidence.py',
         'shopee-monthly':'build_shopee_monthly_from_evidence.py', 'weekly':'build_weekly_from_evidence.py'}


class ProfitCodeBinding(unittest.TestCase):
    def test_optimized_binding_and_drift_rejections_without_business_import(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            runtime = root/'config/tool_runtime_manifest.json'
            runtime.parent.mkdir()
            runtime.write_text(json.dumps({'schema':'orbit-tool-runtime/v1'}), encoding='utf8')
            domain = 'domains/data_operations/profit_settlement/cli.py'
            dependency = 'domains/data_operations/profit_settlement/shared_inputs.py'
            originals = {}
            for name in [*(SKILL+n for n in NAMES.values()), domain, dependency]:
                path = root/name
                path.parent.mkdir(parents=True, exist_ok=True)
                # Any accidental business module execution fails loudly.
                path.write_text("raise RuntimeError('BUSINESS_IMPORT_FORBIDDEN')\n", encoding='utf8')
                originals[name] = path.read_bytes()
            pins = {'schema':'orbit-profit-entry-dependencies/v1','hash_format':'raw-sha256',
                    'entries':{k:SKILL+n for k,n in NAMES.items()},
                    'files':{name:hashlib.sha256(raw).hexdigest() for name,raw in originals.items()}}
            manifest = root/'profit-pins.json'
            def save():
                manifest.write_text(json.dumps(pins), encoding='utf8')
                return hashlib.sha256(manifest.read_bytes()).hexdigest()
            def invoke(sha=None, runtime_sha=None):
                return subprocess.run([sys.executable,'-O','-I','-B',str(ENTRY),
                    '--runtime-root',str(root),'--expected-runtime-file-sha',
                    runtime_sha or hashlib.sha256(runtime.read_bytes()).hexdigest(),
                    '--profit-dependency-manifest',str(manifest),
                    '--expected-profit-dependency-sha',sha or save(),'--check-binding'],
                    capture_output=True,text=True,timeout=5)
            result = invoke()
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(value['dependency_files_verified'], 6)
            self.assertFalse(value['domain_imported'])
            self.assertEqual(value['business_calls'], 0)
            for name in [SKILL+NAMES['report'], dependency]:
                with self.subTest(drift=name):
                    (root/name).write_bytes(b'changed')
                    result = invoke()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('PROFIT_DEPENDENCY_CHANGED', result.stderr)
                    (root/name).write_bytes(originals[name])
            with self.subTest(missing_dependency=True):
                (root/dependency).unlink()
                result=invoke()
                self.assertIn('PROFIT_BINDING_FILE_MISSING', result.stderr)
                (root/dependency).write_bytes(originals[dependency])
            with self.subTest(manifest_drift=True):
                result=invoke(sha='0'*64)
                self.assertIn('PROFIT_DEPENDENCY_MANIFEST_CHANGED', result.stderr)
            with self.subTest(runtime_drift=True):
                result=invoke(runtime_sha='0'*64)
                self.assertIn('RUNTIME_MANIFEST_CHANGED', result.stderr)
            with self.subTest(path_escape=True):
                pins['files']['../outside.py']='0'*64
                result=invoke()
                self.assertIn('PROFIT_DEPENDENCY_PATH_INVALID', result.stderr)


if __name__ == '__main__':
    unittest.main()
