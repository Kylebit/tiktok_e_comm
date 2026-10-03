"""Inspect, compare or explicitly export a reviewed local knowledge snapshot."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from modules.product_agent import knowledge
from shared_platform.capability_runtime import checked_path

PROFILE_SCHEMA='product-publication-knowledge-profile/v1'


def _object(path: Path) -> dict:
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict):raise ValueError('JSON object required: '+path.name)
    return data


def load_profile(project_root: Path, profile_path: Path) -> dict:
    root=knowledge._root(project_root);data=_object(checked_path(root,profile_path))
    required={'schema_version','project_id','vault_root','source_id','section','review_manifest','expected_review_digest'}
    if set(data)-{'expected_knowledge_version'}!=required or data['schema_version']!=PROFILE_SCHEMA:
        raise ValueError('knowledge profile requires explicit source/review fields only; no secrets or provider settings')
    if not isinstance(data['project_id'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',data['project_id']):
        raise ValueError('stable project_id required')
    knowledge._relative(data['section']);knowledge._relative(data['review_manifest'])
    knowledge._digest(data['expected_review_digest'])
    if 'expected_knowledge_version' in data:knowledge._digest(data['expected_knowledge_version'])
    path=Path(data['vault_root'])
    data['selected_vault']=knowledge._root(path) if path.is_absolute() else checked_path(root,path)
    return data


def parser():
    result=argparse.ArgumentParser(description='显式知识来源与已审清单；默认 export-preview，不写 Vault 或快照')
    result.add_argument('action',choices=['inspect','parity','export-preview'],nargs='?',default='export-preview')
    result.add_argument('--project-root',type=Path,required=True)
    result.add_argument('--runtime-root',type=Path,default=ROOT)
    result.add_argument('--profile',type=Path)
    result.add_argument('--vault',type=Path,help='显式源，优先于 profile；仍必须匹配已核准清单')
    result.add_argument('--section',type=Path)
    result.add_argument('--source-id')
    result.add_argument('--review-manifest',type=Path,help='必须位于显式 project root 内')
    result.add_argument('--expected-review-digest')
    result.add_argument('--expected-version')
    result.add_argument('--obsidian-config',type=Path,help='仅显式选择时读取注册表文件；多 Vault 不猜最近打开项')
    result.add_argument('--snapshot',type=Path,help='项目根内已有不可变快照')
    result.add_argument('--query',default='')
    result.add_argument('--replay',action='store_true',help='inspect 的历史只读回放，不能作为运行批准')
    result.add_argument('--reference',action='store_true',help='仅导出或检索已审核参考内容；必须固定版本，不能作为运行规则')
    result.add_argument('--output',type=Path,help='项目根内的新版本文件，不能位于源 Vault')
    result.add_argument('--write',action='store_true',help='export-preview 后显式原子写入本地新文件；保留旧字节')
    return result


def main(argv=None) -> int:
    args=parser().parse_args(argv)
    write_attempted=False
    try:
        if knowledge._root(args.runtime_root).resolve()!=ROOT:raise ValueError('runtime-root must match this exact launcher')
        project=knowledge._root(args.project_root)
        if args.write and args.action!='export-preview':raise ValueError('--write is only supported with export-preview')
        if args.replay and (args.action!='inspect' or args.snapshot is None):raise ValueError('--replay requires inspect --snapshot')
        if args.replay and args.reference:raise ValueError('--replay and --reference are mutually exclusive')
        profile=load_profile(project,args.profile) if args.profile else {}
        expected_version=args.expected_version or profile.get('expected_knowledge_version')
        expected_review=args.expected_review_digest or profile.get('expected_review_digest')
        snapshot_path=checked_path(project,args.snapshot) if args.snapshot else None
        if args.action=='inspect' and snapshot_path is not None:
            base=knowledge.KnowledgeBase.from_path(snapshot_path,expected_version=expected_version,
                expected_review_digest=None if args.replay else expected_review,
                mode='replay' if args.replay else 'reference' if args.reference else 'runtime')
            result={'ok':True,'action':'inspect','snapshot_path':str(snapshot_path),'provenance':base.provenance(),
                'matches':[match.as_dict() for match in base.search(args.query)],'local_write_count':0,'provider_call_count':0}
        else:
            if args.vault is not None:
                vault=knowledge._root(args.vault) if args.vault.is_absolute() else checked_path(project,args.vault)
                selection='explicit_vault'
            elif profile:
                vault=profile['selected_vault'];selection='explicit_profile'
            elif args.obsidian_config is not None:
                vault=knowledge.discover_obsidian_vault(obsidian_config=checked_path(project,args.obsidian_config));selection='explicit_registry'
            else:raise ValueError('choose an explicit --vault or knowledge --profile; default preview does not discover/read a real Vault')
            section=args.section or Path(profile.get('section',knowledge.DEFAULT_SECTION))
            source_id=args.source_id or profile.get('source_id','explicit-source')
            manifest_path=args.review_manifest or profile.get('review_manifest')
            manifest=_object(checked_path(project,manifest_path)) if manifest_path else None
            kwargs=dict(vault_root=vault,section=section,source_id=source_id,review_manifest=manifest,expected_review_digest=expected_review)
            inspection=knowledge.inspect_sources(**kwargs)
            if args.action=='inspect':
                result={'ok':inspection['ready'],'action':'inspect','selection':selection,'inspection':inspection,'local_write_count':0,'provider_call_count':0}
            else:
                export_mode='reference' if args.reference else 'runtime'
                snapshot=knowledge.build_snapshot(**kwargs,mode=export_mode)
                base=knowledge.KnowledgeBase(snapshot,expected_version=expected_version or
                    (snapshot['knowledge_version'] if args.reference and args.action=='export-preview' else None),
                    expected_review_digest=expected_review,mode=export_mode)
                if args.action=='parity':
                    if snapshot_path is None:raise ValueError('parity requires --snapshot inside project root')
                    previous=knowledge.KnowledgeBase.from_path(snapshot_path,expected_version=expected_version,
                        expected_review_digest=expected_review,mode=export_mode)
                    same=previous.version==base.version
                    result={'ok':same,'action':'parity','snapshot_version':previous.version,'source_version':base.version,
                        'provenance':base.provenance(),'local_write_count':0,'provider_call_count':0}
                else:
                    target=checked_path(project,args.output) if args.output else None
                    existed=target.exists() if target else False
                    if args.write:
                        write_attempted=True
                        knowledge.sync_snapshot(target,**kwargs,output_root=project,write=True,mode=export_mode)
                    result={'ok':True,'action':'export-preview','mode':'LOCAL_WRITE' if args.write else 'PREVIEW',
                        'knowledge_version':base.version,'document_count':len(snapshot['documents']),'inspection':inspection,
                        'output':str(target) if target else None,'provenance':base.provenance(),
                        'local_write_count':int(args.write and not existed),'provider_call_count':0}
        print(json.dumps(result,ensure_ascii=False,sort_keys=True))
        return 0 if result['ok'] else 1
    except (OSError,ValueError,TypeError,KeyError) as error:
        state=getattr(error,'output_commit_state','UNKNOWN' if write_attempted else 'NOT_ATTEMPTED')
        print(json.dumps({'ok':False,'error_type':type(error).__name__,'error':str(error),
            'output_commit_state':state,'local_write_count':1 if state=='PUBLISHED' else 0 if state in {'NOT_ATTEMPTED','NOT_PUBLISHED'} else None,
            'provider_call_count':0},ensure_ascii=False))
        return 2


if __name__=='__main__':raise SystemExit(main())
