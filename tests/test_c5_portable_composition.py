"""Real launchers in isolated projects must consume the combined code package."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def cli(root, project, script, *args):
    environment = {k: os.environ[k] for k in ('SystemRoot', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'PATHEXT') if k in os.environ}
    environment.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    result = subprocess.run([sys.executable, '-B', str(root/'scripts'/script), *map(str, args)],
                          cwd=project, env=environment, capture_output=True, text=True, encoding='utf-8', timeout=30)
    evidence=project/'cli-results';evidence.mkdir(exist_ok=True)
    record={'command':result.args,'cwd':str(project),'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr}
    (evidence/(str(len(list(evidence.iterdir())))+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def test_combined_cli_accepts_preserved_s02d_checkpoint(tmp_path):
    profile={'schema':'orbit-tool-profile/v1','tenant_id':'c5','artifact_root':'artifacts','providers':{}}
    (tmp_path/'profile.json').write_text(json.dumps(profile), encoding='utf-8')
    result=cli(ROOT,tmp_path,'orbit_tools.py','--runtime-root',ROOT,'--project-root',tmp_path,
               '--profile','profile.json','doctor','--capability','duoplus')
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['ok'] is True


def test_portable_package_exposes_pinned_knowledge_without_shadow_runtime(tmp_path):
    package=tmp_path/'package'
    built=cli(ROOT,tmp_path,'package_agent_tools.py','--runtime-root',ROOT,'--destination',package,'--build')
    assert built.returncode == 0, built.stdout + built.stderr
    help_result=cli(package,tmp_path,'sync_product_publication_knowledge.py','--help')
    assert help_result.returncode == 0, help_result.stdout + help_result.stderr
    assert 'expected-version' in help_result.stdout
    assert not (package/'modules/product_agent/runtime.py').exists()
    assert not (package/'modules/product_agent/store.py').exists()
