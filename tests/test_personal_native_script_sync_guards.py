"""Synthetic files only: the actual personal Skill directory is never accessed."""
from pathlib import Path
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/sync_personal_native_workflow_scripts.py'
spec = importlib.util.spec_from_file_location('four_sync_guard_test_subject', SCRIPT)
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def inventory(root):
    return {str(p.relative_to(root)): digest(p.read_bytes())
            for p in root.rglob('*') if p.is_file()}


class OptimizedSyncGuardTests(unittest.TestCase):
    def fixture(self, root):
        source, personal, backup = (root / name for name in ('source', 'personal', 'backup'))
        rows = {}
        for index, (relative, source_relative) in enumerate(subject.FILES.items()):
            src, dst = source / source_relative, personal / relative
            src.parent.mkdir(parents=True, exist_ok=True)
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.write_bytes(f'def synthetic():\n    return {index}\n'.encode())
            dst.write_bytes(f'# original synthetic {index}\r\n'.encode())
            rows[relative] = {'source_sha256': digest(src.read_bytes()),
                              'before_sha256': digest(dst.read_bytes())}
        (personal / 'unrelated.txt').write_bytes(b'untouched personal synthetic bytes\r\n')
        dependency = source / 'shared_platform/synthetic_binding.py'
        dependency.parent.mkdir()
        dependency.write_bytes(b'# synthetic dependency\n')
        plan = {'source_root': str(source), 'personal_root': str(personal),
                'backup_root': str(backup), 'files': rows,
                'runtime_dependencies': {'shared_platform/synthetic_binding.py': digest(dependency.read_bytes())}}
        return plan, source, personal, backup

    def run_sync(self, root, plan, *, apply=True, wrong_plan_hash=False):
        path = root / 'plan.json'
        path.write_text(json.dumps(plan), encoding='utf8')
        argv = [sys.executable, '-I', '-B', '-O', '-X', 'utf8', str(SCRIPT),
                '--plan', str(path), '--plan-sha',
                '0' * 64 if wrong_plan_hash else digest(path.read_bytes())]
        if apply:
            argv.append('--apply')
        return subprocess.run(argv, capture_output=True, text=True, encoding='utf8', timeout=10)

    def test_optimized_python_rejects_bound_input_drift_before_any_write(self):
        cases = ('plan_hash', 'scope', 'source', 'before', 'dependency', 'traversal', 'relative_root', 'overlapping_root')
        codes = ('PLAN_SHA_MISMATCH', 'FOUR_FILE_SCOPE_REQUIRED', 'SOURCE_SHA_MISMATCH',
                 'BEFORE_SHA_MISMATCH', 'DEPENDENCY_SHA_MISMATCH', 'RELATIVE_PATH_OUT_OF_SCOPE',
                 'ROOT_MUST_BE_ABSOLUTE', 'ROOTS_OVERLAP')
        for case, code in zip(cases, codes):
            with self.subTest(case=case), tempfile.TemporaryDirectory(prefix='orbit-four-sync-test-') as directory:
                root = Path(directory)
                plan, source, personal, backup = self.fixture(root)
                relative, source_relative = next(iter(subject.FILES.items()))
                if case == 'scope':
                    plan['files']['fifth/scripts/extra.py'] = plan['files'][relative].copy()
                elif case == 'source':
                    (source / source_relative).write_bytes(b'# source drift\n')
                elif case == 'before':
                    (personal / relative).write_bytes(b'# before drift\n')
                elif case == 'dependency':
                    plan['runtime_dependencies']['shared_platform/synthetic_binding.py'] = '0' * 64
                elif case == 'traversal':
                    plan['runtime_dependencies'] = {'../outside.py': '0' * 64}
                elif case == 'relative_root':
                    plan['source_root'] = 'relative-source'
                elif case == 'overlapping_root':
                    plan['backup_root'] = str(personal / 'backup')
                before = (inventory(source), inventory(personal))
                result = self.run_sync(root, plan, wrong_plan_hash=case == 'plan_hash')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(code, result.stderr)
                self.assertEqual((inventory(source), inventory(personal)), before)
                self.assertFalse(backup.exists())

    def test_default_is_readonly_under_optimized_python(self):
        with tempfile.TemporaryDirectory(prefix='orbit-four-sync-test-') as directory:
            root = Path(directory)
            plan, source, personal, backup = self.fixture(root)
            before = inventory(personal)
            result = self.run_sync(root, plan, apply=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {'status': 'FOUR_FILE_PREFLIGHT_PASSED', 'apply': False})
            self.assertEqual(inventory(personal), before)
            self.assertFalse(backup.exists())

    def test_apply_preserves_exact_originals_and_rejects_repeat_under_optimized_python(self):
        with tempfile.TemporaryDirectory(prefix='orbit-four-sync-test-') as directory:
            root = Path(directory)
            plan, source, personal, backup = self.fixture(root)
            original = {relative: (personal / relative).read_bytes() for relative in subject.FILES}
            unrelated = (personal / 'unrelated.txt').read_bytes()
            result = self.run_sync(root, plan)
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads((backup / 'receipt.json').read_text(encoding='utf8'))
            self.assertEqual(set(receipt['originals']), set(subject.FILES))
            self.assertEqual([row['path'] for row in receipt['changed']], list(subject.FILES))
            for relative, source_relative in subject.FILES.items():
                self.assertEqual((backup / relative).read_bytes(), original[relative])
                self.assertEqual((personal / relative).read_bytes(), (source / source_relative).read_bytes())
            self.assertEqual((personal / 'unrelated.txt').read_bytes(), unrelated)
            after = inventory(root)
            result = self.run_sync(root, plan)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('BEFORE_SHA_MISMATCH', result.stderr)
            self.assertEqual(inventory(root), after)


if __name__ == '__main__':
    unittest.main()
