"""Select an original profit CLI inside an explicitly pinned complete runtime."""
from pathlib import Path, PurePosixPath
import argparse,hashlib,json,os,re,stat,sys
ENTRIES={'report':'profit_report.py','tiktok-monthly':'build_tiktok_monthly_from_evidence.py','shopee-monthly':'build_shopee_monthly_from_evidence.py','weekly':'build_weekly_from_evidence.py'}
def regular(path):
 for item in (path,*path.parents):
  try: info=item.lstat()
  except OSError: raise SystemExit('PROFIT_BINDING_FILE_MISSING') from None
  if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400: raise SystemExit('PROFIT_BINDING_REPARSE_REJECTED')
 info=path.stat()
 if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1: raise SystemExit('PROFIT_BINDING_REGULAR_FILE_REQUIRED')
 return path.read_bytes()

def verify_profit(root,manifest,expected,entry):
 if not manifest.is_absolute(): raise SystemExit('ABSOLUTE_PROFIT_MANIFEST_REQUIRED')
 raw=regular(manifest)
 if hashlib.sha256(raw).hexdigest()!=expected: raise SystemExit('PROFIT_DEPENDENCY_MANIFEST_CHANGED')
 try: pins=json.loads(raw)
 except (ValueError,UnicodeError): raise SystemExit('PROFIT_DEPENDENCY_MANIFEST_INVALID') from None
 files=pins.get('files'); entries={k:'domains/data_operations/skills/manage-profit-settlement/scripts/'+v for k,v in ENTRIES.items()}
 if (pins.get('schema')!='orbit-profit-entry-dependencies/v1' or pins.get('hash_format')!='raw-sha256' or pins.get('entries')!=entries or not isinstance(files,dict) or not files or any(p not in files for p in entries.values()) or 'domains/data_operations/profit_settlement/cli.py' not in files): raise SystemExit('PROFIT_DEPENDENCY_MANIFEST_INVALID')
 for name,sha in files.items():
  relative=PurePosixPath(name)
  if (relative.is_absolute() or relative.as_posix()!=name or '\\' in name or ':' in name or '..' in relative.parts or not isinstance(sha,str) or not re.fullmatch('[0-9a-f]{64}',sha)): raise SystemExit('PROFIT_DEPENDENCY_PATH_INVALID')
  path=root.joinpath(*relative.parts); data=regular(path)
  if not path.resolve().is_relative_to(root.resolve()): raise SystemExit('PROFIT_DEPENDENCY_OUTSIDE_RUNTIME')
  if hashlib.sha256(data).hexdigest()!=sha: raise SystemExit('PROFIT_DEPENDENCY_CHANGED')
 return files[entries[entry]],len(files)

def main():
 p=argparse.ArgumentParser();p.add_argument('--runtime-root',type=Path,required=True);p.add_argument('--expected-runtime-file-sha',required=True);p.add_argument('--profit-dependency-manifest',type=Path,required=True);p.add_argument('--expected-profit-dependency-sha',required=True);p.add_argument('--entry',choices=ENTRIES,default='report');p.add_argument('--check-binding',action='store_true');p.add_argument('arguments',nargs=argparse.REMAINDER);a=p.parse_args()
 root=a.runtime_root
 if not root.is_absolute():raise SystemExit('ABSOLUTE_RUNTIME_REQUIRED')
 for x in(root,*root.parents):
  if x.exists() and (stat.S_ISLNK(x.lstat().st_mode) or getattr(x.lstat(),'st_file_attributes',0)&0x400):raise SystemExit('RUNTIME_REPARSE_REJECTED')
 manifest=root/'config/tool_runtime_manifest.json'
 runtime=regular(manifest)
 if hashlib.sha256(runtime).hexdigest()!=a.expected_runtime_file_sha:raise SystemExit('RUNTIME_MANIFEST_CHANGED')
 if json.loads(runtime).get('schema')!='orbit-tool-runtime/v1':raise SystemExit('RUNTIME_MANIFEST_SCHEMA_INVALID')
 entry_sha,count=verify_profit(root,a.profit_dependency_manifest,a.expected_profit_dependency_sha,a.entry)
 domain=root/'domains/data_operations/profit_settlement'
 if not (domain/'cli.py').is_file():raise SystemExit('COMPLETE_PROFIT_DOMAIN_REQUIRED')
 target=root/'domains/data_operations/skills/manage-profit-settlement/scripts'/ENTRIES[a.entry]
 if not target.is_file():raise SystemExit('ORIGINAL_PROFIT_ENTRY_MISSING')
 # Original wrappers resolve their actual full-repository parent themselves.
 if not target.resolve().is_relative_to(root.resolve()):raise SystemExit('PROFIT_ENTRY_OUTSIDE_RUNTIME')
 if a.check_binding:
  print(json.dumps({'status':'REPO_BOUND_ENTRY_CODE_VALIDATED','runtime_root':str(root),'entry':str(target),'entry_sha256':entry_sha,'dependency_files_verified':count,'domain_imported':False,'provider_calls':0,'business_calls':0}));return
 args=a.arguments
 if args and args[0]=='--':args=args[1:]
 if not args:raise SystemExit('EXPLICIT_PROFIT_ARGUMENTS_REQUIRED')
 # Recheck original code pins immediately before handing off, without importing domains.
 if hashlib.sha256(regular(manifest)).hexdigest()!=a.expected_runtime_file_sha:raise SystemExit('RUNTIME_MANIFEST_CHANGED')
 verify_profit(root,a.profit_dependency_manifest,a.expected_profit_dependency_sha,a.entry)
 # No parameters or business authority are generated or defaulted here.
 os.execv(sys.executable,[sys.executable,'-I','-B',str(target),*args])
if __name__=='__main__':main()
