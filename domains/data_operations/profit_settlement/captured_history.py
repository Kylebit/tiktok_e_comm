"""Read existing approved artifacts; never write or change approval ownership."""
from pathlib import Path
from .knowledge_base import ProfitKnowledgeBase, _checksum
from .captured_coverage import read_local

def captured_history(profile):
    result={'status':'not_selected','entries':[],'issues':[]}
    if not profile.get('knowledge_root'):return result
    try:
        root=Path(profile['knowledge_root']).resolve()
        if not Path(profile['knowledge_root']).is_absolute():raise ValueError()
        rows=ProfitKnowledgeBase(root).list_reports(platform=profile['platform'])
        if len(rows)>500:raise ValueError()
        for row in rows:
            try:
                path=(root/row['artifact_path']).resolve()
                if not path.is_relative_to(root) or path==root:raise ValueError()
                artifact,file_sha=read_local(path)
                report,approval=artifact['report'],artifact['approval']
                digest=_checksum(report)
                knowledge_id='profit-knowledge-'+_checksum({'report':digest,'approval':approval})[:20]
                if (artifact.get('schema_version')!='profit-knowledge-entry/v1' or approval.get('status')!='APPROVED'
                    or approval.get('report_checksum')!=digest or approval!=row.get('approval')
                    or approval.get('audit',{}).get('status')!='PASSED' or approval.get('audit',{}).get('report_digest')!='sha256:'+digest
                    or knowledge_id!=row.get('knowledge_id') or knowledge_id!=artifact.get('knowledge_id')
                    or report.get('report_id')!=row.get('report_id') or not report.get('report_id')
                    or report.get('period_kind')!='monthly' or report.get('status')!='ready'
                    or report.get('platform')!=profile['platform'] or artifact.get('platform')!=profile['platform']
                    or not approval.get('approved_by') or not approval.get('approved_at')):raise ValueError()
                identities={(line.get('identity',{}).get('region'),line.get('identity',{}).get('shop_id')) for line in report.get('order_lines',[])}
                # The old index has no site/shop columns. Inspect frozen report
                # identities; do not infer from its folder, label or current profile.
                if not identities or any(not all(pair) for pair in identities):
                    result['issues'].append('historical_scope_unproven');continue
                if identities!={(profile['site'],profile['shop_id'])}:continue
                result['entries'].append({'knowledge_id':knowledge_id,'report_id':report['report_id'],'approval_status':'APPROVED',
                    'approval':approval,'scope':{'platform':profile['platform'],'site':profile['site'],'shop_id':profile['shop_id'],**report['period']},
                    'report_sha256':digest,'artifact_sha256':file_sha,'source':report.get('source'),
                    'report':report})
            except (OSError,ValueError,KeyError,TypeError,AttributeError):result['issues'].append('historical_artifact_unverified')
        result['status']='read_only_index'
    except (OSError,ValueError,KeyError,TypeError,AttributeError):result['status']='unavailable';result['issues'].append('historical_index_unavailable')
    return result
