"""Build only the portable desktop client from an explicit source allowlist.

No dependency installation, repository-wide data collection or cleanup occurs.
The complete engineering project and its configuration remain external.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(output: Path):
    if os.name != 'nt' or platform.machine().lower() not in {'amd64','x86_64'}:
        raise RuntimeError('This manifest builds Windows x64 only.')
    if not output.is_absolute():
        raise ValueError('Use an absolute build output path.')
    output = output.resolve()
    if output.exists():
        raise FileExistsError('Choose a new output directory; existing builds are preserved: ' + str(output))
    manifest = json.loads((ROOT/'desktop/build_manifest.json').read_text(encoding='utf-8'))
    lock = ROOT/'desktop/requirements-build.lock.txt'
    versions = {}
    for line in lock.read_text().splitlines():
        if not line or line.startswith('#'): continue
        requirement=line.split()[0]
        name,expected=requirement.split('==')
        version=importlib.metadata.version(name)
        if version!=expected:
            raise RuntimeError(f'{name}: expected {expected}, found {version}; use the isolated lock file.')
        versions[name]=version
    # Copy individual declared files into a new minimal analysis tree. Unlisted
    # repository files cannot be discovered by PyInstaller's module analysis.
    output.mkdir(parents=True)
    stage=output/'source'; stage.mkdir()
    records=[]
    for relative in manifest['source_files']+manifest['identity_sources']:
        source=(ROOT/relative).resolve(strict=True)
        if not source.is_relative_to(ROOT) or not source.is_file():
            raise ValueError('Source escapes the selected project: '+relative)
        destination=stage/relative
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,destination)
        records.append({'path':relative,'sha256':sha(source),'bytes':source.stat().st_size})
    hooks=output/'hooks'; hooks.mkdir()
    webview_root=Path(importlib.util.find_spec('webview').origin).parent
    data=[(str(webview_root/name),'webview/'+str(Path(name).parent).replace('\\','/')) for name in manifest['webview_data']]
    binaries=[(str(webview_root/name),'webview/'+str(Path(name).parent).replace('\\','/')) for name in manifest['webview_binaries']]
    for source,_ in data+binaries:
        if not Path(source).is_file(): raise FileNotFoundError(source)
    (hooks/'hook-webview.py').write_text('datas = '+repr(data)+'\nbinaries = '+repr(binaries)+'\n',encoding='utf-8')
    identity=[(str(stage/name),str(Path(name).parent)) for name in manifest['identity_sources']]
    excludes=['shared_platform','core','scripts.product_publication_runtime','tkinter','PyQt5','PyQt6',
              'PySide2','PySide6','gi','cefpython3','webview.platforms.android','webview.platforms.cocoa',
              'webview.platforms.gtk','webview.platforms.qt','webview.platforms.cef','pytest','unittest',
              'jinja2','markupsafe']
    spec=output/'OrbitDesktop.spec'
    spec.write_text(f'''import json
from pathlib import Path
a = Analysis([{str(stage/'scripts/start_orbit_desktop.py')!r}], pathex=[{str(stage)!r}],
    binaries=[], datas={identity!r}, hiddenimports=['webview.platforms.edgechromium'],
    hookspath=[{str(hooks)!r}], excludes={excludes!r}, noarchive=False)
Path({str(output/'analysis-manifest.json')!r}).write_text(json.dumps({{'pure':list(a.pure),'binaries':list(a.binaries),'datas':list(a.datas)}},indent=2),encoding='utf-8')
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='OrbitDesktop',debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='OrbitDesktop')
''',encoding='utf-8')
    command=[sys.executable,'-B','-m','PyInstaller','--noconfirm','--distpath',str(output/'dist'),
             '--workpath',str(output/'work'),str(spec)]
    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYINSTALLER_CONFIG_DIR':str(output/'pyinstaller-cache')}
    # The caller's cwd and PYTHONPATH must not expose another source checkout.
    env['PYTHONPATH']=os.pathsep.join(p for p in sys.path if p and not Path(p).resolve().is_relative_to(ROOT))
    receipt={'schema':manifest['schema'],'product':'portable-client','root':str(ROOT),'output':str(output),
        'python':sys.executable,'versions':versions,'inputs':records,'command':command,
        'dependency_lock_sha256':sha(lock),'business_service_bundled':False}
    with (output/'build.log').open('w',encoding='utf-8') as log:
        completed=subprocess.run(command,cwd=stage,env=env,stdout=log,stderr=subprocess.STDOUT,check=False)
    receipt['exit_code']=completed.returncode
    bundle=output/'dist/OrbitDesktop'
    if completed.returncode == 0:
        for relative,destination in manifest['documents'].items():
            source=(ROOT/relative).resolve(strict=True)
            target=(bundle/destination).resolve()
            if not source.is_relative_to(ROOT) or not target.is_relative_to(bundle):
                raise ValueError('Document path outside allowlist roots')
            shutil.copyfile(source,target)
            receipt['inputs'].append({'path':relative,'sha256':sha(source),'bytes':source.stat().st_size})
    receipt['bundle_files']=[{'path':p.relative_to(bundle).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)}
                             for p in sorted(bundle.rglob('*')) if p.is_file()] if bundle.exists() else []
    (output/'build-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    if completed.returncode:
        raise RuntimeError('Build failed; preserved log: '+str(output/'build.log'))
    # Verify collected project inputs, including compiled modules. Dependency
    # files have independent recorded origins; no broad repository data glob.
    analysis=json.loads((output/'analysis-manifest.json').read_text())
    allowed={str((stage/name).resolve()) for name in manifest['source_files']+manifest['identity_sources']}
    for kind,rows in analysis.items():
        for _,source,*metadata in rows:
            # PyInstaller represents namespace packages as PYMODULE with source
            # '-'. It is an in-memory package marker, never a filesystem input.
            if kind == 'pure' and source == '-' and metadata == ['PYMODULE']:
                continue
            path=Path(source).resolve()
            if path.is_relative_to(ROOT) and not path.is_relative_to(output) and str(path) not in allowed:
                raise RuntimeError('Undeclared repository input: '+str(path))
            if path.is_relative_to(stage) and str(path) not in allowed:
                raise RuntimeError('Undeclared staged input: '+str(path))
    print(str(bundle/'OrbitDesktop.exe'))
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True,help='New absolute directory; never overwritten')
    args=parser.parse_args(argv)
    return build(args.output)


if __name__=='__main__':
    raise SystemExit(main())
