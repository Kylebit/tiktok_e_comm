"""An actual isolated source identity for parent-worker runtime fixtures."""
from pathlib import Path
import os
import shutil
import subprocess

from shared_platform.operations_runtime import RuntimeProfile, runtime_manifest, runtime_matches


SOURCE_PATHS = (
    'shared_platform/native_parent_category.py',
    'shared_platform/native_parent_facts.py',
    'shared_platform/native_parent_child_receipt.py',
    'shared_platform/native_task_preparation.py',
    'shared_platform/operations_runtime.py',
    'shared_platform/operations_publication.py',
    'shared_platform/operations_publication_prepare.py',
    'shared_platform/workbench_publication_native.py',
    'shared_platform/round1_workspace.py',
    'shared_platform/worker_category_admission.py',
    'shared_platform/worker_category_agent_attempts.py',
    'shared_platform/worker_category_bridge.py',
    'shared_platform/worker_category_facts_transport.py',
    'shared_platform/worker_category_intents.py',
    'shared_platform/worker_category_parent_flow.py',
    'shared_platform/worker_category_readonly_cli.py',
    'skills/prepare-product-publication/scripts/prepare_product_publication.py',
)


def owned_profile(root):
    """Pin the real copied producer bytes and Git HEAD, without bypassing checks.

    The Product Center fixture root is intentionally not the candidate checkout.
    Its private Git repository contains only the fixed copied runtime sources;
    no fixture data, credentials, reports or task database is committed.
    """
    root = Path(root).resolve()
    source = Path(__file__).resolve().parents[1]
    executable = shutil.which('git')
    assert executable and Path(executable).is_absolute() and Path(executable).is_file()
    empty = root / '.owned-parent-git-empty'
    empty.mkdir(exist_ok=True)
    config = empty / 'config'
    config.write_bytes(b'')
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('GIT_')}
    environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=str(config))
    for relative in SOURCE_PATHS:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        body = (source / relative).read_bytes()
        if destination.exists():
            assert destination.read_bytes() == body
        else:
            destination.write_bytes(body)
    def git(*args):
        return subprocess.run([executable, '-c', 'core.hooksPath='+str(empty),
            '-c', 'commit.gpgsign=false', '-c', 'tag.gpgsign=false',
            '-c', 'core.autocrlf=false', *args], cwd=root, env=environment,
            capture_output=True, text=True, check=True, timeout=15).stdout.strip()
    if not (root / '.git').exists():
        git('init', '--quiet', '--template='+str(empty))
        git('add', '--', *SOURCE_PATHS)
        git('-c', 'user.name=Owned runtime fixture', '-c',
            'user.email=owned-runtime-fixture@example.invalid', 'commit',
            '--quiet', '-m', 'Pin actual parent runtime fixture sources')
    version = git('rev-parse', 'HEAD')
    profile = RuntimeProfile(root, root/'data/operations/stable', 'stable',
                             version, runtime_manifest(root))
    assert profile.manifest_digest and runtime_matches(profile)
    return profile
