"""Rollbackable four-file personal Skill sync; static file operations only."""
from pathlib import Path
import argparse,ast,hashlib,json,os,stat,uuid,datetime
FILES={
 'prepare-product-publication/scripts/prepare_product_publication.py':'skills/prepare-product-publication/scripts/prepare_product_publication.py',
 'prepare-product-images/scripts/prepare_product_images.py':'skills/prepare-product-images/scripts/prepare_product_images.py',
 'prepare-product-images/scripts/run_automated_image_qa.py':'skills/prepare-product-images/scripts/run_automated_image_qa.py',
 'delist-products-by-sku/scripts/delist_products_by_sku.py':'skills/delist-products-by-sku/scripts/delist_products_by_sku.py'}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def checked(p):
 for item in (p,*p.parents):
  if item.exists():
   s=item.lstat()
   if stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0)&0x400:raise ValueError('LINK_OR_REPARSE_PATH')
 if p.exists() and p.is_file() and p.stat().st_nlink!=1:raise ValueError('HARDLINK_FILE')
 return p

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--plan',type=Path,required=True);ap.add_argument('--plan-sha',required=True);ap.add_argument('--apply',action='store_true');a=ap.parse_args()
 checked(a.plan);assert sha(a.plan)==a.plan_sha;plan=json.loads(a.plan.read_text(encoding='utf8'))
 source=Path(plan['source_root']);personal=Path(plan['personal_root']);backup=Path(plan['backup_root']);checked(source);checked(personal);checked(backup)
 assert set(plan['files'])==set(FILES)
 for relative,sourcerel in FILES.items():
  src=checked(source/sourcerel);dst=checked(personal/relative);row=plan['files'][relative]
  assert sha(src)==row['source_sha256'] and sha(dst)==row['before_sha256']
  ast.parse(src.read_text(encoding='utf-8-sig'))
 for path,h in plan['runtime_dependencies'].items():assert sha(checked(source/path))==h
 if not a.apply:print(json.dumps({'status':'FOUR_FILE_PREFLIGHT_PASSED','apply':False}));return
 backup.mkdir(exist_ok=False)
 originals={}
 for relative in FILES:
  dst=personal/relative;data=dst.read_bytes();saved=backup/relative;saved.parent.mkdir(parents=True,exist_ok=True)
  with saved.open('xb')as stream:stream.write(data)
  assert sha(saved)==plan['files'][relative]['before_sha256'];originals[relative]={'backup':str(saved),'sha256':sha(saved),'root_link_type':'REGULAR_DIRECTORY','resolved_root':str((personal/relative.split('/')[0]).resolve()),'file_attributes':dst.lstat().st_file_attributes,'mode':dst.stat().st_mode}
 receipt={'schema':'orbit-four-personal-script-sync/v1','started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'BACKED_UP_BEFORE_ANY_REPLACE','source_root':str(source),'personal_root':str(personal),'originals':originals,'changed':[],'provider_calls':0,'business_calls':0,'sql_connections':0,'dependency_check':'file SHA and AST only, no domain/provider import','rollback':'Restore exact original bytes from originals only if current hash equals recorded installed hash; keep receipts and unrelated Skill contents.'}
 receiptpath=backup/'receipt.json';receiptpath.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
 for relative,sourcerel in FILES.items():
  src=checked(source/sourcerel);dst=checked(personal/relative);row=plan['files'][relative];assert sha(src)==row['source_sha256']and sha(dst)==row['before_sha256']
  temp=dst.with_name(dst.name+'.native-sync-'+uuid.uuid4().hex)
  with temp.open('xb')as stream:stream.write(src.read_bytes());stream.flush();os.fsync(stream.fileno())
  assert sha(temp)==row['source_sha256'];os.replace(temp,dst);assert sha(dst)==row['source_sha256']
  receipt['changed'].append({'path':relative,'installed_sha256':sha(dst)})
  receiptpath.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
 receipt['status']='FOUR_SCRIPTS_INSTALLED_FILE_VALIDATED_NO_BUSINESS_EXECUTION';receipt['completed_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();receiptpath.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(json.dumps({'receipt':str(receiptpath),'sha256':sha(receiptpath),'status':receipt['status']},ensure_ascii=False))
if __name__=='__main__':main()