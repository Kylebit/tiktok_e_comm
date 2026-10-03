"""Rollbackable four-file personal Skill sync; static file operations only."""
from pathlib import Path, PureWindowsPath
import argparse,ast,hashlib,json,os,stat,uuid,datetime,re
FILES={
 'prepare-product-publication/scripts/prepare_product_publication.py':'skills/prepare-product-publication/scripts/prepare_product_publication.py',
 'prepare-product-images/scripts/prepare_product_images.py':'skills/prepare-product-images/scripts/prepare_product_images.py',
 'prepare-product-images/scripts/run_automated_image_qa.py':'skills/prepare-product-images/scripts/run_automated_image_qa.py',
 'delist-products-by-sku/scripts/delist_products_by_sku.py':'skills/delist-products-by-sku/scripts/delist_products_by_sku.py'}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def require(condition,code):
 if not condition:raise ValueError(code)

def digest(value):
 require(isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value) is not None,'INVALID_SHA256')
 return value

def absolute_root(value):
 require(isinstance(value,str) and bool(value),'INVALID_ROOT')
 p=Path(value)
 require(p.is_absolute() and '..' not in p.parts,'ROOT_MUST_BE_ABSOLUTE')
 return checked(p)

def relative_path(value):
 require(isinstance(value,str) and bool(value),'INVALID_RELATIVE_PATH')
 p=Path(value);windows=PureWindowsPath(value)
 require(not p.is_absolute() and not windows.drive and not windows.root and '..' not in windows.parts and '..' not in p.parts,'RELATIVE_PATH_OUT_OF_SCOPE')
 require(':' not in value,'RELATIVE_PATH_OUT_OF_SCOPE')
 return p

def file_hash(p):
 checked(p);require(p.is_file(),'EXPECTED_REGULAR_FILE')
 return sha(p)

def checked(p):
 for item in (p,*p.parents):
  if item.exists() or item.is_symlink():
   s=item.lstat()
   if stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0)&0x400:raise ValueError('LINK_OR_REPARSE_PATH')
 if p.exists() and p.is_file() and p.stat().st_nlink!=1:raise ValueError('HARDLINK_FILE')
 return p

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--plan',type=Path,required=True);ap.add_argument('--plan-sha',required=True);ap.add_argument('--apply',action='store_true');a=ap.parse_args()
 checked(a.plan);require(a.plan.is_file(),'EXPECTED_REGULAR_FILE');planbytes=a.plan.read_bytes()
 require(hashlib.sha256(planbytes).hexdigest()==digest(a.plan_sha),'PLAN_SHA_MISMATCH')
 try:plan=json.loads(planbytes.decode('utf8'))
 except (UnicodeError,json.JSONDecodeError):raise ValueError('INVALID_PLAN_JSON') from None
 require(isinstance(plan,dict),'INVALID_PLAN')
 require(all(key in plan for key in ('source_root','personal_root','backup_root','files','runtime_dependencies')),'PLAN_FIELDS_MISSING')
 source=absolute_root(plan['source_root']);personal=absolute_root(plan['personal_root']);backup=absolute_root(plan['backup_root'])
 roots=[p.resolve() for p in (source,personal,backup)]
 require(all(left!=right and left not in right.parents and right not in left.parents for index,left in enumerate(roots) for right in roots[index+1:]),'ROOTS_OVERLAP')
 require(isinstance(plan['files'],dict) and set(plan['files'])==set(FILES),'FOUR_FILE_SCOPE_REQUIRED')
 require(isinstance(plan['runtime_dependencies'],dict),'INVALID_RUNTIME_DEPENDENCIES')
 for relative,sourcerel in FILES.items():
  src=checked(source/sourcerel);dst=checked(personal/relative);row=plan['files'][relative]
  require(isinstance(row,dict) and 'source_sha256' in row and 'before_sha256' in row,'INVALID_FILE_BINDING')
  require(file_hash(src)==digest(row['source_sha256']),'SOURCE_SHA_MISMATCH')
  require(file_hash(dst)==digest(row['before_sha256']),'BEFORE_SHA_MISMATCH')
  ast.parse(src.read_text(encoding='utf-8-sig'))
 for path,h in plan['runtime_dependencies'].items():require(file_hash(source/relative_path(path))==digest(h),'DEPENDENCY_SHA_MISMATCH')
 if not a.apply:print(json.dumps({'status':'FOUR_FILE_PREFLIGHT_PASSED','apply':False}));return
 require(not backup.exists(),'BACKUP_ALREADY_EXISTS')
 backup.mkdir(exist_ok=False)
 originals={}
 for relative in FILES:
  dst=personal/relative;data=dst.read_bytes();saved=backup/relative;saved.parent.mkdir(parents=True,exist_ok=True)
  with saved.open('xb')as stream:stream.write(data)
  require(file_hash(saved)==plan['files'][relative]['before_sha256'],'BACKUP_SHA_MISMATCH');originals[relative]={'backup':str(saved),'sha256':sha(saved),'root_link_type':'REGULAR_DIRECTORY','resolved_root':str((personal/relative.split('/')[0]).resolve()),'file_attributes':getattr(dst.lstat(),'st_file_attributes',0),'mode':dst.stat().st_mode}
 receipt={'schema':'orbit-four-personal-script-sync/v1','started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'BACKED_UP_BEFORE_ANY_REPLACE','source_root':str(source),'personal_root':str(personal),'originals':originals,'changed':[],'provider_calls':0,'business_calls':0,'sql_connections':0,'dependency_check':'file SHA and AST only, no domain/provider import','rollback':'Restore exact original bytes from originals only if current hash equals recorded installed hash; keep receipts and unrelated Skill contents.'}
 receiptpath=backup/'receipt.json';receiptpath.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
 for relative,sourcerel in FILES.items():
  src=checked(source/sourcerel);dst=checked(personal/relative);row=plan['files'][relative]
  require(file_hash(src)==row['source_sha256'],'SOURCE_SHA_MISMATCH');require(file_hash(dst)==row['before_sha256'],'BEFORE_SHA_MISMATCH')
  temp=dst.with_name(dst.name+'.native-sync-'+uuid.uuid4().hex)
  with temp.open('xb')as stream:stream.write(src.read_bytes());stream.flush();os.fsync(stream.fileno())
  require(file_hash(temp)==row['source_sha256'],'TEMP_SHA_MISMATCH');os.replace(temp,dst);require(file_hash(dst)==row['source_sha256'],'INSTALLED_SHA_MISMATCH')
  receipt['changed'].append({'path':relative,'installed_sha256':sha(dst)})
  receiptpath.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
 receipt['status']='FOUR_SCRIPTS_INSTALLED_FILE_VALIDATED_NO_BUSINESS_EXECUTION';receipt['completed_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();receiptpath.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(json.dumps({'receipt':str(receiptpath),'sha256':sha(receiptpath),'status':receipt['status']},ensure_ascii=False))
if __name__=='__main__':main()
