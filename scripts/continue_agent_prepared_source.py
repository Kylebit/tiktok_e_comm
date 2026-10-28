"""Explicit same-task source compatibility continuation; no provider transport."""
from pathlib import Path
import argparse,json,os,socket,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--deployment',type=Path,required=True)
    p.add_argument('--receipt',type=Path,required=True)
    p.add_argument('--common-create-successor',action='store_true',help='Append the decision-bound v2 link, retaining v1 and UNKNOWN1')
    p.add_argument('--initialize-common-successor-schema',action='store_true',help='Explicit local child-ledger schema maintenance')
    p.add_argument('--common-create-correction',action='store_true',help='Append receipt-bound v3 for one known missing-price correction')
    p.add_argument('--common-baseline-successor',action='store_true',help='Append new-decision-bound v4 retaining original baseline UNKNOWN')
    p.add_argument('--common-baseline-lock-fix',action='store_true',help='Append local-lock-only v5 reusing the original v4 decision and unchanged EDIT budget')
    p.add_argument('--common-colormap-correction',action='store_true',help='Append standing-authority-derived v6 for one captured colorMap rejection correction')
    p.add_argument('--completed-common-gallery-compatibility',action='store_true',help='Append one source-only frozen gallery compatibility event after verified COMMON; no new EDIT budget')
    p.add_argument('--completed-common-variant-image-projection',action='store_true',help='Append the bounded second gallery-source revision for frozen marketplace variant image projection; no business writes')
    p.add_argument('--api-only',action='store_true',help='Bind the exact two startup-pinned R35/API inputs into the same second source event; no new approval or business write')
    p.add_argument('--native-final-image-ui-compatibility',action='store_true',help='Append one independent frozen native-final UI source event; retain both gallery events and the existing candidate')
    p.add_argument('--ui-mechanism-head',help='Exact reviewed bootstrap commit, followed only by the two approved frontend files')
    p.add_argument('--ui-candidate-digest',help='Exact already persisted candidate digest; no compilation or approval')
    p.add_argument('--ui-request',type=Path,help='Absolute immutable user UI repair request, not marketplace approval')
    p.add_argument('--ui-request-sha256',help='SHA256 of the exact user UI repair request')
    p.add_argument('--approved-native-r1-reader-compatibility',action='store_true',help='Append one technical reader source event preserving the original APPROVED native decision; no new approval')
    p.add_argument('--r1-reader-mechanism-head',help='Exact integrated mechanism commit, followed only by the reviewed reader and its test')
    p.add_argument('--r1-reader-mechanism-manifest',help='Captured runtime manifest of the mechanism commit before reader integration')
    p.add_argument('--r1-reader-candidate-digest',help='Exact already approved frozen candidate digest')
    p.add_argument('--r1-reader-work-order',type=Path,help='Absolute immutable R46 technical continuation work order')
    p.add_argument('--r1-reader-work-order-sha256',help='SHA256 of the immutable R46 work order')
    p.add_argument('--r1-reader-preimage',type=Path,help='Absolute original post-approval preservation preimage')
    p.add_argument('--r1-reader-preimage-sha256',help='SHA256 of the original approval preimage')
    p.add_argument('--approved-platform-preparation-compatibility',action='store_true',help='Append one source-only compatibility event retaining original failed attempts and real Ozon imports')
    p.add_argument('--platform-mechanism-head',help='Exact reviewed mechanism commit before the four bounded compatibility files')
    p.add_argument('--platform-mechanism-manifest',help='Runtime manifest captured at that mechanism commit')
    p.add_argument('--platform-technical-request',type=Path,help='Immutable original-approved platform technical compatibility request')
    p.add_argument('--platform-technical-request-sha256')
    p.add_argument('--platform-run-preimage',type=Path,help='Immutable original terminal run, failure, submission and budget preimage')
    p.add_argument('--platform-run-preimage-sha256')
    p.add_argument('--platform-ozon-readback',type=Path,help='Immutable official readback of the three original Ozon imports')
    p.add_argument('--platform-ozon-readback-sha256')
    p.add_argument('--native-tiktok-ozon-recovery-compatibility',action='store_true',help='Append one combined native-wrapper/media recovery source event; bind explicit media3 authorization without creating its grant or budget')
    p.add_argument('--native-recovery-mechanism-head')
    p.add_argument('--native-recovery-mechanism-manifest')
    p.add_argument('--native-recovery-technical-request',type=Path)
    p.add_argument('--native-recovery-technical-request-sha256')
    p.add_argument('--native-recovery-run-preimage',type=Path)
    p.add_argument('--native-recovery-run-preimage-sha256')
    p.add_argument('--ozon-media3-decision',type=Path)
    p.add_argument('--ozon-media3-decision-sha256')
    p.add_argument('--approved-stock-category-continuation',action='store_true')
    p.add_argument('--stock-category-mechanism-head')
    p.add_argument('--stock-category-mechanism-manifest')
    p.add_argument('--stock-category-technical-request',type=Path)
    p.add_argument('--stock-category-technical-request-sha256')
    p.add_argument('--stock-category-run-preimage',type=Path)
    p.add_argument('--stock-category-run-preimage-sha256')
    p.add_argument('--approved-tiktok-locale-draft-correction',action='store_true',help='Append one source-only localized draft correction event; preserve original reserved publish slots and UNKNOWN history')
    p.add_argument('--locale-draft-mechanism-head')
    p.add_argument('--locale-draft-mechanism-manifest')
    p.add_argument('--locale-draft-technical-request',type=Path)
    p.add_argument('--locale-draft-technical-request-sha256')
    p.add_argument('--locale-draft-run-preimage',type=Path)
    p.add_argument('--locale-draft-run-preimage-sha256')
    p.add_argument('--tiktok-draft-correction-proof',type=Path)
    p.add_argument('--tiktok-draft-correction-proof-sha256')
    p.add_argument('--approved-tiktok-activation-connection-fix',action='store_true')
    p.add_argument('--tiktok-activation-mechanism-head')
    p.add_argument('--tiktok-activation-mechanism-manifest')
    p.add_argument('--tiktok-activation-technical-request',type=Path)
    p.add_argument('--tiktok-activation-technical-request-sha256')
    p.add_argument('--tiktok-activation-run-preimage',type=Path)
    p.add_argument('--tiktok-activation-run-preimage-sha256')
    p.add_argument('--approved-shopee-regional-tiktok-saved-publish-continuation',action='store_true',help='Bind existing SH regional work and accepted-SAVE unsent TK publication; no SAVE, shared write, PH create or budget')
    p.add_argument('--shopee-regional-mechanism-head')
    p.add_argument('--shopee-regional-mechanism-manifest')
    p.add_argument('--shopee-regional-technical-request',type=Path)
    p.add_argument('--shopee-regional-technical-request-sha256')
    p.add_argument('--shopee-regional-run-preimage',type=Path)
    p.add_argument('--shopee-regional-run-preimage-sha256')
    p.add_argument('--shopee-regional-proof',type=Path)
    p.add_argument('--shopee-regional-proof-sha256')
    p.add_argument('--tiktok-saved-publish-proof',type=Path)
    p.add_argument('--tiktok-saved-publish-proof-sha256')
    p.add_argument('--approved-ozon-stock-shopee-frozen-completion-continuation',action='store_true',help='Bind original Ozon stock and exact frozen Shopee copy/description projection completion then listing; no new content, budget or create')
    p.add_argument('--completion-mechanism-head')
    p.add_argument('--completion-mechanism-manifest')
    p.add_argument('--completion-technical-request',type=Path)
    p.add_argument('--completion-technical-request-sha256')
    p.add_argument('--completion-run-preimage',type=Path)
    p.add_argument('--completion-run-preimage-sha256')
    p.add_argument('--ozon-two-card-proof',type=Path)
    p.add_argument('--ozon-two-card-proof-sha256')
    p.add_argument('--shopee-existing-item-completion-proof',type=Path)
    p.add_argument('--shopee-existing-item-completion-proof-sha256')
    p.add_argument('--initialize-common-correction-schema',action='store_true',help='Explicit correction-ledger schema maintenance')
    args=p.parse_args(argv);config=json.loads(args.deployment.read_text(encoding='utf-8-sig'))
    if args.api_only and (not args.completed_common_variant_image_projection or config.get('agent_prepared_api_target_scope') is None):
        raise ValueError('API_SCOPE_EXPLICIT_COMBINED_ACTION_AND_TWO_PINS_REQUIRED')
    if args.native_final_image_ui_compatibility != bool(args.ui_mechanism_head and args.ui_candidate_digest and args.ui_request and args.ui_request_sha256) or (not args.native_final_image_ui_compatibility and (args.ui_mechanism_head or args.ui_candidate_digest or args.ui_request or args.ui_request_sha256)):
        raise ValueError('NATIVE_FINAL_UI_EXACT_SOURCE_AND_CANDIDATE_REQUIRED')
    reader_args=(args.r1_reader_mechanism_head,args.r1_reader_mechanism_manifest,args.r1_reader_candidate_digest,
        args.r1_reader_work_order,args.r1_reader_work_order_sha256,args.r1_reader_preimage,args.r1_reader_preimage_sha256)
    if (args.approved_native_r1_reader_compatibility and not all(reader_args)) or (not args.approved_native_r1_reader_compatibility and any(reader_args)):
        raise ValueError('R1_READER_EXACT_APPROVED_SOURCE_AND_PINS_REQUIRED')
    platform_args=(args.platform_mechanism_head,args.platform_mechanism_manifest,args.platform_technical_request,args.platform_technical_request_sha256,
        args.platform_run_preimage,args.platform_run_preimage_sha256,args.platform_ozon_readback,args.platform_ozon_readback_sha256)
    if (args.approved_platform_preparation_compatibility and not all(platform_args)) or (not args.approved_platform_preparation_compatibility and any(platform_args)):
        raise ValueError('PLATFORM_SOURCE_EXACT_APPROVED_HISTORY_PINS_REQUIRED')
    native_recovery_args=(args.native_recovery_mechanism_head,args.native_recovery_mechanism_manifest,args.native_recovery_technical_request,
        args.native_recovery_technical_request_sha256,args.native_recovery_run_preimage,args.native_recovery_run_preimage_sha256,
        args.ozon_media3_decision,args.ozon_media3_decision_sha256)
    if (args.native_tiktok_ozon_recovery_compatibility and not all(native_recovery_args)) or (not args.native_tiktok_ozon_recovery_compatibility and any(native_recovery_args)):
        raise ValueError('NATIVE_RECOVERY_SOURCE_EXACT_HISTORY_PINS_REQUIRED')
    stock_category_args=(args.stock_category_mechanism_head,args.stock_category_mechanism_manifest,args.stock_category_technical_request,
        args.stock_category_technical_request_sha256,args.stock_category_run_preimage,args.stock_category_run_preimage_sha256)
    if (args.approved_stock_category_continuation and not all(stock_category_args)) or (not args.approved_stock_category_continuation and any(stock_category_args)):
        raise ValueError('STOCK_CATEGORY_SOURCE_EXACT_HISTORY_PINS_REQUIRED')
    locale_draft_args=(args.locale_draft_mechanism_head,args.locale_draft_mechanism_manifest,args.locale_draft_technical_request,
        args.locale_draft_technical_request_sha256,args.locale_draft_run_preimage,args.locale_draft_run_preimage_sha256,
        args.tiktok_draft_correction_proof,args.tiktok_draft_correction_proof_sha256)
    if (args.approved_tiktok_locale_draft_correction and not all(locale_draft_args)) or (not args.approved_tiktok_locale_draft_correction and any(locale_draft_args)):
        raise ValueError('LOCALE_DRAFT_SOURCE_EXACT_HISTORY_PINS_REQUIRED')
    activation_args=(args.tiktok_activation_mechanism_head,args.tiktok_activation_mechanism_manifest,args.tiktok_activation_technical_request,
        args.tiktok_activation_technical_request_sha256,args.tiktok_activation_run_preimage,args.tiktok_activation_run_preimage_sha256)
    if (args.approved_tiktok_activation_connection_fix and not all(activation_args)) or (not args.approved_tiktok_activation_connection_fix and any(activation_args)):
        raise ValueError('TIKTOK_ACTIVATION_SOURCE_EXACT_HISTORY_PINS_REQUIRED')
    regional_args=(args.shopee_regional_mechanism_head,args.shopee_regional_mechanism_manifest,args.shopee_regional_technical_request,
        args.shopee_regional_technical_request_sha256,args.shopee_regional_run_preimage,args.shopee_regional_run_preimage_sha256,
        args.shopee_regional_proof,args.shopee_regional_proof_sha256,args.tiktok_saved_publish_proof,args.tiktok_saved_publish_proof_sha256)
    if (args.approved_shopee_regional_tiktok_saved_publish_continuation and not all(regional_args)) or (not args.approved_shopee_regional_tiktok_saved_publish_continuation and any(regional_args)):
        raise ValueError('SHOPEE_REGIONAL_SOURCE_EXACT_HISTORY_PINS_REQUIRED')
    two_card_args=(args.completion_mechanism_head,args.completion_mechanism_manifest,args.completion_technical_request,
        args.completion_technical_request_sha256,args.completion_run_preimage,args.completion_run_preimage_sha256,
        args.ozon_two_card_proof,args.ozon_two_card_proof_sha256,args.shopee_existing_item_completion_proof,args.shopee_existing_item_completion_proof_sha256)
    if (args.approved_ozon_stock_shopee_frozen_completion_continuation and not all(two_card_args)) or (not args.approved_ozon_stock_shopee_frozen_completion_continuation and any(two_card_args)):
        raise ValueError('COMBINED_COMPLETION_SOURCE_EXACT_HISTORY_PINS_REQUIRED')
    if (sum((args.common_create_successor,args.common_create_correction,args.common_baseline_successor,args.common_baseline_lock_fix,args.common_colormap_correction,args.completed_common_gallery_compatibility,args.completed_common_variant_image_projection,args.native_final_image_ui_compatibility,args.approved_native_r1_reader_compatibility,args.approved_platform_preparation_compatibility,args.native_tiktok_ozon_recovery_compatibility,args.approved_stock_category_continuation,args.approved_tiktok_locale_draft_correction,args.approved_tiktok_activation_connection_fix,args.approved_shopee_regional_tiktok_saved_publish_continuation,args.approved_ozon_stock_shopee_frozen_completion_continuation))>1
        or args.initialize_common_correction_schema and not args.common_create_correction):
        raise ValueError('SOURCE_SUCCESSOR_EXACT_SOURCE_ACTION_REQUIRED')
    from scripts.stable_runtime_bootstrap import prebind_release_store,prebind_workbench_store,configure_stable_runtime
    from shared_platform.agent_prepared_common_service import SCOPE
    if config.get('native_service_scope')!=SCOPE or Path(config['code_root']).resolve()!=ROOT:
        raise ValueError('SOURCE_SUCCESSOR_EXACT_DEPLOYMENT_REQUIRED')
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(('127.0.0.1',int(config['port'])))==0:
            raise ValueError('SOURCE_SUCCESSOR_ORIGINAL_SERVICE_MUST_BE_STOPPED')
    release=prebind_release_store(config);legacy=prebind_workbench_store(config)
    os.environ['ORBIT_OPERATIONS_ENV']='stable'
    os.environ['ORBIT_OPERATIONS_DATA_ROOT']=config['operations_data_root']
    os.environ['ORBIT_HIVE_SETTINGS']=config['settings']
    os.environ['ORBIT_R3_CONFIG_ROOT']=config.get('publication_runtime_config_root') or config['r3_config_root']
    os.environ['TIKTOK_ECOMM_HOME']=config['r3_config_root']
    from shared_platform.operations_launch import bind_publication_policy_override,bind_review_paths
    bind_publication_policy_override(config);bind_review_paths(config)
    configure_stable_runtime(config,expected_workbench_store_path=legacy,expected_release_store_path=release)
    from shared_platform.operations_runtime import RuntimeProfile
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform.release_store import default_release_store
    from shared_platform.native_windows_actor import NativeActorServiceConfig,bootstrap_service,NativeWindowsActorProfileReader
    from shared_platform.agent_prepared_source_successor import continue_source
    profile=RuntimeProfile.capture(ROOT)
    if profile.version!=config['code_version'] or profile.manifest_digest!=config['manifest_digest']:
        raise ValueError('SOURCE_SUCCESSOR_DEPLOYMENT_SOURCE_CHANGED')
    if not args.receipt.is_absolute() or args.receipt.exists():raise ValueError('SOURCE_SUCCESSOR_NEW_ABSOLUTE_RECEIPT_REQUIRED')
    engine=WorkbenchEngine(profile.data_root/'tasks.db',dict(code_version=profile.version,environment='stable',manifest_digest=profile.manifest_digest))
    actor=bootstrap_service(NativeActorServiceConfig(profile.data_root/'native-final-owner','Kyle'))
    kwargs=dict(task_id=config['agent_prepared_task_id'],expected_adoption_digest=config['agent_prepared_adoption_digest'],
        actor_reader=NativeWindowsActorProfileReader(actor,actor.grant_same_user()))
    if args.initialize_common_successor_schema and not args.common_create_successor:
        raise ValueError('SOURCE_SUCCESSOR_EXPLICIT_V2_REQUIRED')
    if args.approved_ozon_stock_shopee_frozen_completion_continuation:
        from shared_platform.agent_prepared_source_successor import continue_combined_completion_source
        record=continue_combined_completion_source(engine,profile,default_release_store(),mechanism_head=args.completion_mechanism_head,
            mechanism_manifest=args.completion_mechanism_manifest,
            technical_request=dict(path=str(args.completion_technical_request),sha256=args.completion_technical_request_sha256),
            run_preimage=dict(path=str(args.completion_run_preimage),sha256=args.completion_run_preimage_sha256),
            ozon_two_card_stock_ref=dict(path=str(args.ozon_two_card_proof),sha256=args.ozon_two_card_proof_sha256),
            shopee_existing_item_completion_ref=dict(path=str(args.shopee_existing_item_completion_proof),sha256=args.shopee_existing_item_completion_proof_sha256),**kwargs)
    elif args.approved_shopee_regional_tiktok_saved_publish_continuation:
        from shared_platform.agent_prepared_source_successor import continue_shopee_regional_source
        record=continue_shopee_regional_source(engine,profile,default_release_store(),mechanism_head=args.shopee_regional_mechanism_head,
            mechanism_manifest=args.shopee_regional_mechanism_manifest,
            technical_request=dict(path=str(args.shopee_regional_technical_request),sha256=args.shopee_regional_technical_request_sha256),
            run_preimage=dict(path=str(args.shopee_regional_run_preimage),sha256=args.shopee_regional_run_preimage_sha256),
            shopee_regional_continuation_ref=dict(path=str(args.shopee_regional_proof),sha256=args.shopee_regional_proof_sha256),
            tiktok_saved_publish_continuation_ref=dict(path=str(args.tiktok_saved_publish_proof),sha256=args.tiktok_saved_publish_proof_sha256),**kwargs)
    elif args.approved_tiktok_activation_connection_fix:
        from shared_platform.agent_prepared_source_successor import continue_tiktok_activation_source
        record=continue_tiktok_activation_source(engine,profile,default_release_store(),mechanism_head=args.tiktok_activation_mechanism_head,
            mechanism_manifest=args.tiktok_activation_mechanism_manifest,
            technical_request=dict(path=str(args.tiktok_activation_technical_request),sha256=args.tiktok_activation_technical_request_sha256),
            run_preimage=dict(path=str(args.tiktok_activation_run_preimage),sha256=args.tiktok_activation_run_preimage_sha256),**kwargs)
    elif args.approved_tiktok_locale_draft_correction:
        from shared_platform.agent_prepared_source_successor import continue_locale_draft_source
        record=continue_locale_draft_source(engine,profile,default_release_store(),mechanism_head=args.locale_draft_mechanism_head,
            mechanism_manifest=args.locale_draft_mechanism_manifest,
            technical_request=dict(path=str(args.locale_draft_technical_request),sha256=args.locale_draft_technical_request_sha256),
            run_preimage=dict(path=str(args.locale_draft_run_preimage),sha256=args.locale_draft_run_preimage_sha256),
            tiktok_draft_correction_ref=dict(path=str(args.tiktok_draft_correction_proof),sha256=args.tiktok_draft_correction_proof_sha256),**kwargs)
    elif args.approved_stock_category_continuation:
        from shared_platform.agent_prepared_source_successor import continue_stock_category_source
        record=continue_stock_category_source(engine,profile,default_release_store(),mechanism_head=args.stock_category_mechanism_head,
            mechanism_manifest=args.stock_category_mechanism_manifest,
            technical_request=dict(path=str(args.stock_category_technical_request),sha256=args.stock_category_technical_request_sha256),
            run_preimage=dict(path=str(args.stock_category_run_preimage),sha256=args.stock_category_run_preimage_sha256),**kwargs)
    elif args.native_tiktok_ozon_recovery_compatibility:
        from shared_platform.agent_prepared_source_successor import continue_native_tiktok_ozon_recovery_source
        record=continue_native_tiktok_ozon_recovery_source(engine,profile,default_release_store(),mechanism_head=args.native_recovery_mechanism_head,
            mechanism_manifest=args.native_recovery_mechanism_manifest,
            technical_request=dict(path=str(args.native_recovery_technical_request),sha256=args.native_recovery_technical_request_sha256),
            run_preimage=dict(path=str(args.native_recovery_run_preimage),sha256=args.native_recovery_run_preimage_sha256),
            ozon_media3_decision_ref=dict(path=str(args.ozon_media3_decision),sha256=args.ozon_media3_decision_sha256),**kwargs)
    elif args.approved_platform_preparation_compatibility:
        from shared_platform.agent_prepared_source_successor import continue_approved_platform_compatibility
        record=continue_approved_platform_compatibility(engine,profile,default_release_store(),mechanism_head=args.platform_mechanism_head,
            mechanism_manifest=args.platform_mechanism_manifest,
            technical_request=dict(path=str(args.platform_technical_request),sha256=args.platform_technical_request_sha256),
            run_preimage=dict(path=str(args.platform_run_preimage),sha256=args.platform_run_preimage_sha256),
            ozon_readback=dict(path=str(args.platform_ozon_readback),sha256=args.platform_ozon_readback_sha256),**kwargs)
    elif args.approved_native_r1_reader_compatibility:
        from shared_platform.agent_prepared_source_successor import continue_approved_native_r1_reader
        record=continue_approved_native_r1_reader(engine,profile,default_release_store(),mechanism_head=args.r1_reader_mechanism_head,
            mechanism_manifest=args.r1_reader_mechanism_manifest,candidate_digest=args.r1_reader_candidate_digest,
            work_order=dict(path=str(args.r1_reader_work_order),sha256=args.r1_reader_work_order_sha256),
            preimage=dict(path=str(args.r1_reader_preimage),sha256=args.r1_reader_preimage_sha256),**kwargs)
    elif args.native_final_image_ui_compatibility:
        from shared_platform.agent_prepared_source_successor import continue_native_final_image_ui
        record=continue_native_final_image_ui(engine,profile,default_release_store(),mechanism_head=args.ui_mechanism_head,
            candidate_digest=args.ui_candidate_digest,user_request=dict(path=str(args.ui_request),sha256=args.ui_request_sha256),**kwargs)
    elif args.completed_common_gallery_compatibility or args.completed_common_variant_image_projection:
        from shared_platform.agent_prepared_source_successor import continue_gallery_source
        record=continue_gallery_source(engine,profile,default_release_store(),variant_image_projection=args.completed_common_variant_image_projection,
            api_scope_pins=config.get('agent_prepared_api_target_scope') if args.api_only else None,**kwargs)
    elif args.common_colormap_correction:
        from shared_platform.agent_prepared_source_successor import continue_sixth_source
        from shared_platform.native_common_colormap_correction import installed_decision
        record=continue_sixth_source(engine,profile,default_release_store(),decision_reader=installed_decision(config),**kwargs)
    elif args.common_baseline_lock_fix:
        from shared_platform.agent_prepared_source_successor import continue_fifth_source
        from shared_platform.native_common_baseline_successor import installed_decision
        record=continue_fifth_source(engine,profile,default_release_store(),decision_reader=installed_decision(config),**kwargs)
    elif args.common_baseline_successor:
        from shared_platform.agent_prepared_source_successor import continue_fourth_source
        from shared_platform.native_common_baseline_successor import installed_decision
        record=continue_fourth_source(engine,profile,default_release_store(),decision_reader=installed_decision(config),**kwargs)
    elif args.common_create_correction:
        from shared_platform.agent_prepared_source_successor import continue_third_source
        from shared_platform.manual_common_successor import installed_correction_decision,initialize_correction_schema
        reader=installed_correction_decision(config)
        record=continue_third_source(engine,profile,default_release_store(),decision_reader=reader,**kwargs)
        if args.initialize_common_correction_schema:initialize_correction_schema(default_release_store().path)
    elif args.common_create_successor:
        from shared_platform.agent_prepared_source_successor import continue_second_source
        from shared_platform.manual_common_successor import installed_decision,initialize_schema
        reader=installed_decision(config)
        record=continue_second_source(engine,profile,default_release_store(),decision_reader=reader,**kwargs)
        if args.initialize_common_successor_schema:initialize_schema(default_release_store().path)
    else:record=continue_source(engine,profile,default_release_store(),**kwargs)
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    args.receipt.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'task_id':record['task_id'],'schema_version':record['schema_version'],
        'continued_at':record['continued_at'],'original_adoption_digest':record['original_adoption_digest'],
        'marketplace_approval':False,'external_write_count':0}))
    return 0


if __name__=='__main__':raise SystemExit(main())
