"""Select an original profit CLI inside an explicitly pinned complete runtime."""
from pathlib import Path
import argparse,hashlib,json,os,stat,sys
ENTRIES={'report':'profit_report.py','tiktok-monthly':'build_tiktok_monthly_from_evidence.py','shopee-monthly':'build_shopee_monthly_from_evidence.py','weekly':'build_weekly_from_evidence.py'}
def main():
 p=argparse.ArgumentParser();p.add_argument('--runtime-root',type=Path,required=True);p.add_argument('--expected-runtime-file-sha',required=True);p.add_argument('--entry',choices=ENTRIES,default='report');p.add_argument('--check-binding',action='store_true');p.add_argument('arguments',nargs=argparse.REMAINDER);a=p.parse_args()
 root=a.runtime_root
 if not root.is_absolute():raise SystemExit('ABSOLUTE_RUNTIME_REQUIRED')
 for x in(root,*root.parents):
  if x.exists() and (stat.S_ISLNK(x.lstat().st_mode) or getattr(x.lstat(),'st_file_attributes',0)&0x400):raise SystemExit('RUNTIME_REPARSE_REJECTED')
 manifest=root/'config/tool_runtime_manifest.json'
 if hashlib.sha256(manifest.read_bytes()).hexdigest()!=a.expected_runtime_file_sha:raise SystemExit('RUNTIME_MANIFEST_CHANGED')
 if json.loads(manifest.read_text(encoding='utf8')).get('schema')!='orbit-tool-runtime/v1':raise SystemExit('RUNTIME_MANIFEST_SCHEMA_INVALID')
 domain=root/'domains/data_operations/profit_settlement'
 if not (domain/'cli.py').is_file():raise SystemExit('COMPLETE_PROFIT_DOMAIN_REQUIRED')
 target=root/'domains/data_operations/skills/manage-profit-settlement/scripts'/ENTRIES[a.entry]
 if not target.is_file():raise SystemExit('ORIGINAL_PROFIT_ENTRY_MISSING')
 # Original wrappers resolve their actual full-repository parent themselves.
 if not target.resolve().is_relative_to(root.resolve()):raise SystemExit('PROFIT_ENTRY_OUTSIDE_RUNTIME')
 if a.check_binding:
  print(json.dumps({'status':'REPO_BOUND_ENTRY_RESOLVES','runtime_root':str(root),'entry':str(target),'entry_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'domain_imported':False,'provider_calls':0,'business_calls':0}));return
 args=a.arguments
 if args and args[0]=='--':args=args[1:]
 if not args:raise SystemExit('EXPLICIT_PROFIT_ARGUMENTS_REQUIRED')
 # No parameters or business authority are generated or defaulted here.
 os.execv(sys.executable,[sys.executable,'-I','-B',str(target),*args])
if __name__=='__main__':main()