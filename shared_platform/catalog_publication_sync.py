"""Durable local outbox between official readback and catalog projection."""
from pathlib import Path
import json
import os
import re
import uuid

from shared_platform.catalog_cost_projection import canonical, digest, project
from domains.product_operations import validate_approved_publication_snapshot


class CatalogPublicationSync:
    def __init__(self, catalog_path, outbox_root):
        self.catalog_path = Path(catalog_path)
        self.root = Path(outbox_root)
        self.failures = []

    def _path(self, receipt_id):
        if type(receipt_id) is not str or not re.fullmatch('[a-f0-9]{64}', receipt_id):
            raise ValueError('invalid_catalog_receipt_id')
        return self.root / (receipt_id + '.json')

    def _write(self, path, value):
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        with temporary.open('x', encoding='utf-8') as stream:
            stream.write(canonical(value));stream.flush();os.fsync(stream.fileno())
        os.replace(temporary, path)

    def begin(self, request):
        """Durable intent before dispatch, including the conservative baseline."""
        snapshot=validate_approved_publication_snapshot(request.snapshot).payload()
        packet={'schema':'catalog-publication-intent/v1','offer_id':snapshot['offer_id'],
                'run_id':request.run_id,'snapshot_digest':snapshot['snapshot_digest'],
                'platform':request.platform,'targets':list(request.target_labels),
                'snapshot':snapshot,'report_id':request.report_id,
                'cost_policy':'LATE_IDENTITY_PRESERVE_EXISTING_COST'}
        receipt_id=digest(packet);path=self._path(receipt_id)
        if path.exists():
            value=json.loads(path.read_text(encoding='utf-8'))
            if value['packet']!=packet:raise ValueError('catalog_intent_conflict')
        else:
            self._write(path,{'packet':packet,'packet_digest':digest(packet),'state':'PENDING_OFFICIAL_EVIDENCE'})
        return receipt_id

    def prepare_shopee(self,request,global_item_id,runtime):
        """Freeze exact query scope before any regional publish request."""
        bindings=[]
        for target in request.target_labels:
            context=runtime.context(target.split(':',1)[1])
            bindings.append({'target_label':target,'region':context.region,'shop_id':str(context.shop_id),'merchant_id':str(context.merchant_id)})
        path=self._path(self.begin(request));value=json.loads(path.read_text(encoding='utf-8'))
        recovery={'global_item_id':str(global_item_id),'bindings':bindings}
        if value.get('recovery') and value['recovery']!=recovery:raise ValueError('catalog_recovery_scope_conflict')
        value.update(recovery=recovery,recovery_digest=digest(recovery));self._write(path,value)

    def record_shopee_dispatch(self,request,dispatch):
        path=self._path(self.begin(request));value=json.loads(path.read_text(encoding='utf-8'))
        # Persist provider task/item IDs before readback; no tokens or browser facts.
        rows=[{key:row.get(key) for key in ('target_label','accepted','provider_task_id','existing_item_id','selected_logistics_ids')} for row in dispatch.get('targets',[])]
        value.update(dispatch=rows,dispatch_digest=digest(rows));self._write(path,value)

    def recover_official(self,receipt_id):
        """Explicit query-only reconciliation; ordinary recover stays local-only."""
        path=self._path(receipt_id);value=json.loads(path.read_text(encoding='utf-8'));packet=value['packet']
        if digest(packet)!=value['packet_digest']:raise ValueError('catalog_intent_integrity_error')
        if packet.get('schema')!='catalog-publication-intent/v1':return self.recover(receipt_id)
        linked=value.get('observation_receipt_id')
        if linked:
            result=self.recover(linked)
            if result['state']=='COMPLETE':return result
        try:
            if packet.get('platform')=='OZON':return self._recover_ozon(packet,value)
            if packet.get('platform')!='SHOPEE':raise ValueError('official_variant_readback_not_connected')
            if digest(value.get('recovery'))!=value.get('recovery_digest') or digest(value.get('dispatch'))!=value.get('dispatch_digest'):
                raise ValueError('exact_persisted_query_identity_missing')
            snapshot=validate_approved_publication_snapshot(packet['snapshot']).payload()
            if snapshot['snapshot_digest']!=packet['snapshot_digest']:raise ValueError('frozen_snapshot_identity_conflict')
            from shared_platform.catalog_shopee_readback import read_catalog_observations
            observations=read_catalog_observations(snapshot,packet['targets'],value['recovery'],value['dispatch'])
            from shared_platform.product_publication_runner import PublicationPlatformRequest
            request=PublicationPlatformRequest(packet['run_id'],packet['report_id'],'SHOPEE',tuple(packet['targets']),snapshot,catalog_sink=self)
            return self.capture(request,observations)
        except Exception as error:
            value.update(state='PENDING_OFFICIAL_EVIDENCE',recovery_error= str(error) if type(error) is ValueError else type(error).__name__)
            self._write(path,value)
            return {'receipt_id':receipt_id,'state':value['state'],'code':value['recovery_error'],'external_write_count':0}

    def prepare_ozon(self,request,account,variants):
        from shared_platform.catalog_ozon_readback import account_identity
        account=account_identity(account)
        approved={s['model_sku']:s['variant_key'] for s in request.snapshot['skus']}
        if len(variants)!=len(approved) or {v['offer_id']:v['variant_key'] for v in variants}!=approved:
            raise ValueError('ozon_frozen_offer_binding_conflict')
        path=self._path(self.begin(request));value=json.loads(path.read_text(encoding='utf-8'))
        recovery={'account':account,'variants':list(variants)}
        if value.get('ozon_recovery') and value['ozon_recovery']!=recovery:raise ValueError('ozon_frozen_recovery_conflict')
        value.update(ozon_recovery=recovery,ozon_recovery_digest=digest(recovery));self._write(path,value)

    def _recover_ozon(self,packet,value):
        recovery=value.get('ozon_recovery')
        if not isinstance(recovery,dict) or digest(recovery)!=value.get('ozon_recovery_digest'):raise ValueError('ozon_exact_persisted_scope_missing')
        snapshot=validate_approved_publication_snapshot(packet['snapshot']).payload()
        if snapshot['snapshot_digest']!=packet['snapshot_digest'] or packet['targets']!=['ozon:RU']:raise ValueError('ozon_frozen_snapshot_conflict')
        expected={s['model_sku']:s['variant_key'] for s in snapshot['skus']}
        variants=recovery['variants']
        if len(variants)!=len(expected) or {v['offer_id']:v['variant_key'] for v in variants}!=expected:raise ValueError('ozon_frozen_offer_binding_conflict')
        from shared_platform.catalog_ozon_readback import recover_observations
        observations=recover_observations(recovery['account'],variants)
        from shared_platform.product_publication_runner import PublicationPlatformRequest
        request=PublicationPlatformRequest(packet['run_id'],packet['report_id'],'OZON',tuple(packet['targets']),snapshot,catalog_sink=self)
        return self.capture(request,observations)

    def capture(self, request, observations):
        """Called with server-owned frozen facts and producer readback only.

        Projection failure leaves the durable observation available to local
        recovery. Failure here must not reclassify marketplace publication.
        """
        snapshot = validate_approved_publication_snapshot(request.snapshot).payload()
        approved = {s['model_sku']: s for s in snapshot['skus']}
        targets = {t['target_label'] for t in snapshot['publication_targets']}
        rows=[];seen=set()
        for source in observations:
            row = dict(source)
            sku = approved.get(row.get('model_sku'))
            full = row.get('identity') or {}
            if row.get('target_label') not in request.target_labels or row.get('target_label') not in targets or not sku:
                rows.append({'authority':'UNAVAILABLE','verified':False,'reason':'frozen_variant_or_target_unbound'});continue
            if row.get('verified') is True and (full.get('platform') != request.platform.lower() or full.get('seller_sku') != sku['model_sku']):
                rows.append({'authority':'UNAVAILABLE','verified':False,'reason':'official_variant_binding_conflict'});continue
            row['approved_cost'] = sku['cost']['amount'] if sku['cost'].get('currency') == 'CNY' else None
            row['variant_key'] = sku['variant_key']
            row['expected_cost_version'] = None  # late binding: preserve existing costs
            binding=(row['target_label'],row['model_sku'])
            if binding in seen:
                raise ValueError('duplicate_frozen_catalog_binding')
            seen.add(binding)
            rows.append(row)
        for target in request.target_labels:
            for model_sku in approved:
                if (target,model_sku) not in seen:
                    rows.append({'authority':'UNAVAILABLE','verified':False,'target_label':target,'model_sku':model_sku,'reason':'official_variant_readback_missing'})
        receipt_id = digest({'run_id':request.run_id,'snapshot':snapshot['snapshot_digest'],'platform':request.platform,'rows':observations})
        packet={'schema':'catalog-publication-observation/v1','receipt_id':receipt_id,'snapshot_digest':snapshot['snapshot_digest'],'offer_id':snapshot['offer_id'],'product_revision':snapshot['product_revision'],'rows':rows}
        if request.platform=='OZON':
            intent=json.loads(self._path(self.begin(request)).read_text(encoding='utf-8'))
            recovery=intent.get('ozon_recovery')
            if not recovery or digest(recovery)!=intent.get('ozon_recovery_digest'):raise ValueError('ozon_exact_persisted_scope_missing')
            account=recovery['account']['account_id']
            if len(rows)!=len(approved) or any(row.get('verified') is not True or row.get('identity',{}).get('shop_key')!=account for row in rows):raise ValueError('ozon_official_scope_conflict')
            packet['schema']='catalog-ozon-observation/v1'
        path=self._path(receipt_id)
        if path.exists():
            existing=json.loads(path.read_text(encoding='utf-8'))
            if existing['packet'] != packet:raise ValueError('catalog_outbox_identity_conflict')
        else:
            self._write(path,{'packet':packet,'packet_digest':digest(packet),'state':'PENDING_LOCAL_PROJECTION'})
        result=self.recover(receipt_id)
        intent=self._path(self.begin(request))
        recorded=json.loads(intent.read_text(encoding='utf-8'))
        recorded.update(state='OBSERVATION_RECORDED',observation_receipt_id=receipt_id)
        self._write(intent,recorded)
        return result

    def recover(self, receipt_id):
        path=self._path(receipt_id)
        value=json.loads(path.read_text(encoding='utf-8'))
        if value['packet'].get('schema')=='catalog-publication-intent/v1':
            if digest(value['packet'])!=value['packet_digest']:raise ValueError('catalog_intent_integrity_error')
            return {'receipt_id':receipt_id,'state':value['state'],
                    'observation_receipt_id':value.get('observation_receipt_id'),
                    'external_write_count':0,'requires_official_readback':value['state']=='PENDING_OFFICIAL_EVIDENCE'}
        if digest(value['packet'])!=value['packet_digest'] or value['packet']['receipt_id']!=receipt_id:
            raise ValueError('catalog_outbox_integrity_error')
        try:
            result=project(self.catalog_path,value['packet'])
            value.update(state=result['status'],result=result)
        except Exception as error:
            value.update(state='PENDING_LOCAL_PROJECTION',error=type(error).__name__)
        self._write(path,value)
        return {'receipt_id':receipt_id,'state':value['state'],'result':value.get('result'),'external_write_count':0}

    def status(self):
        if not self.root.is_dir():return []
        rows=[]
        for path in sorted(self.root.glob('*.json')):
            value=json.loads(path.read_text(encoding='utf-8'))
            if digest(value['packet'])!=value['packet_digest']:
                raise ValueError('catalog_outbox_integrity_error')
            rows.append({'receipt_id':path.stem,'state':value['state'],'offer_id':value['packet']['offer_id'],'result':value.get('result'),
                         'recovery_error':value.get('recovery_error'),
                         'observation_receipt_id':value.get('observation_receipt_id')})
        return rows


def capture_readback(request, observations):
    sink=getattr(request,'catalog_sink',None)
    if sink is None:return
    try:
        sink.capture(request,observations)
    except Exception:
        # The existing durable publication run remains the reconciliation
        # anchor if even the local outbox cannot be written. Never throw into
        # provider outcome classification or dispatch again here.
        sink.failures.append({'run_id':request.run_id,'code':'CATALOG_EVIDENCE_PERSISTENCE_FAILED'})
