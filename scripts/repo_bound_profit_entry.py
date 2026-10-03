"""Select an original profit CLI inside an explicitly pinned complete runtime."""
from pathlib import Path
import argparse,hashlib,json,os,stat,sys
ENTRIES={'report':'profit_report.py','tiktok-monthly':'build_tiktok_monthly_from_evidence.py','shopee-monthly':'build_shopee_monthly_from_evidence.py','weekly':'build_weekly_from_evidence.py'}
def main():
 p=argparse.ArgumentParser();p.add_argument('--runtime-root',type=Path,required=True);p.add_argument('--expected-runtime-file-sha',required=True);p.add_argument('--entry',choices=ENTRIES,default='report');p.add_argument('--check-binding',action='store_true');p.add_argument('arguments',nargs=argparse.REMAINDER);a=p.parse_args()
 root=a.runtime_root
 assert root.is_absolute()
 for x in(root,*root.parents):
  if x.exists():assert not stat.S_ISLNK(x.lstat().st_mode) and not getattr(x.lstat(),'st_file_attributes',0)&0x400
 manifest=root/'config/tool_runtime_manifest.json';assert hashlib.sha256(manifest.read_bytes()).hexdigest()==a.expected_runtime_file_sha
 assert json.loads(manifest.read_text(encoding='utf8'))['schema']=='orbit-tool-runtime/v1'
 domain=root/'domains/data_operations/profit_settlement';assert (domain/'cli.py').is_file()
 target=root/'domains/data_operations/skills/manage-profit-settlement/scripts'/ENTRIES[a.entry];assert target.is_file()
 # Original wrappers resolve their actual full-repository parent themselves.
 assert target.resolve().is_relative_to(root.resolve())
 if a.check_binding:
  print(json.dumps({'status':'REPO_BOUND_ENTRY_RESOLVES','runtime_root':str(root),'entry':str(target),'entry_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'domain_imported':False,'provider_calls':0,'business_calls':0}));return
 args=a.arguments
 if args and args[0]=='--':args=args[1:]
 if not args:raise SystemExit('EXPLICIT_PROFIT_ARGUMENTS_REQUIRED')
 # No parameters or business authority are generated or defaulted here.
 os.execv(sys.executable,[sys.executable,'-I','-B',str(target),*args])
if __name__=='__main__':main()