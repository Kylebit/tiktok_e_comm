"""Internal exact JPEG representation proof; no network, image or DB writer.

An explicit local ref is input, never success authority. The failed-publish
consumer mints this opaque object only after capturing its current SDK reads.
Every use rechecks the original frozen/R2 identity and all file pins. Only a
working media copy is normalized; this makes no historical upload-origin claim.
"""
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from urllib.parse import urlsplit

_SEAL=object()

def _need(value,code):
    if not value:raise ValueError('TIKTOK_MEDIA_'+code)

def _json(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def _sha(value):return sha256(_json(value).encode()).hexdigest()

def _read(ref,*,image=False):
    from shared_platform.immutable_approval_files import require_local_path
    _need(type(ref) is dict and set(ref)=={'path','sha256'},'PINNED_LOCAL_REF_REQUIRED')
    path=Path(ref['path']);_need(path.is_absolute() and not path.anchor.startswith('\\\\'),'ABSOLUTE_LOCAL_REF_REQUIRED')
    require_local_path(path,root=path.anchor)
    _need(path.is_file() and path.stat().st_size<=(20*1024*1024 if image else 2*1024*1024),'REF_SIZE_INVALID')
    raw=path.read_bytes();_need(sha256(raw).hexdigest()==ref['sha256'],'REF_BYTES_CHANGED')
    return raw

def _context(facts):
    snapshot=facts['snapshot'];plan=facts['plan'];r2=plan['payload']['r3_marketplace_binding']['r2_identity']
    budget=json.loads(facts['scope']['binding_json'])
    _need(r2['offer_id']==snapshot['offer_id'],'R2_OFFER_CHANGED')
    return {'offer_id':snapshot['offer_id'],'plan_id':plan['plan_id'],'payload_digest':plan['payload_digest'],
        'snapshot_digest':snapshot['snapshot_digest'],'candidate_digest':budget['candidate_digest'],
        'product_revision':snapshot['product_revision'],'r2_identity_digest':r2['identity_digest']}

def _codec():
    from PIL import __version__,features
    return {'pillow_version':__version__,'jpeg_codec_version':features.version_codec('jpg'),
        'libjpeg_turbo_version':features.version_feature('libjpeg_turbo'),'optimize':False}

def _object_uri(url):
    parsed=urlsplit(url)
    _need(parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password and not parsed.fragment,'PUBLIC_HTTPS_OBJECT_REQUIRED')
    value=parsed.path.lstrip('/').split('~',1)[0]
    _need(bool(value) and '%' not in value and '..' not in value.split('/'),'OBJECT_URI_INVALID')
    return value

def _media(detail,ordered,assets):
    from shared_platform.product_description_media import html_image_urls
    images=detail.get('main_images') or detail.get('images')
    _need(type(images) is list and len(images)==len(ordered),'CURRENT_GALLERY_COVERAGE_CHANGED')
    for image,row in zip(images,ordered):
        urls=image.get('urls') or image.get('url_list')
        asset=assets[row['asset_index']]
        _need(type(urls) is list and urls and urls[0]==asset['cdn_url']
            and image.get('uri')==row['official_uri'] and _object_uri(urls[0])==row['official_uri'],
            'CURRENT_GALLERY_URI_OR_ORDER_CHANGED')
    description=html_image_urls(detail['description'])
    _need(len(description)==len(ordered) and tuple(_object_uri(url) for url in description)
        ==tuple(row['official_uri'] for row in ordered),'DESCRIPTION_OBJECT_URI_OR_ORDER_CHANGED')
    for image,url in zip(images,description):
        urls=image.get('urls') or image.get('url_list')
        # Bind each description origin to this actual gallery position, rather
        # than trusting an unrelated host that happens to copy the object path.
        def origin(value):
            _object_uri(value);parsed=urlsplit(value)
            return parsed.scheme,parsed.hostname,parsed.port or 443
        _need(origin(url) in {origin(value) for value in urls},'DESCRIPTION_ORIGIN_NOT_IN_CURRENT_GALLERY')
    return description

@dataclass(frozen=True)
class _ExactEncoding:
    seal:object
    key:str

@dataclass(frozen=True)
class _VerifiedMedia:
    seal:object
    context_sha256:str
    identity_sha256:str
    label:str
    document_json:str
    pins:tuple
    summary_json:str

def _encode_once(asset,cache):
    from PIL import Image,JpegImagePlugin
    key=_sha({'asset':asset,'codec':_codec()})
    previous=cache.get(key)
    if type(previous) is _ExactEncoding and previous.seal is _SEAL:return
    source_raw=_read(asset['source_ref'],image=True);jpeg_raw=_read(asset['cdn_ref'],image=True)
    source=Image.open(BytesIO(source_raw));current=Image.open(BytesIO(jpeg_raw));source.load();current.load()
    # No alpha flattening, colorspace/profile/orientation/resize transform is
    # allowed. Even an opaque RGBA source is outside this narrow RGB contract.
    for image,format_ in ((source,'PNG'),(current,'JPEG')):
        _need(image.format==format_ and image.mode=='RGB' and not image.info.get('icc_profile')
            and not any(k in image.info for k in ('gamma','srgb','chromaticity','transparency'))
            and not image.getexif() and not image.info.get('exif'),'COLOR_ALPHA_OR_ORIENTATION_UNPROVEN')
    _need(source.size==current.size,'DIMENSIONS_CHANGED')
    params={'qtables':current.quantization,'sampling':JpegImagePlugin.get_sampling(current),
        'progressive':bool(current.info.get('progressive') or current.info.get('progression'))}
    # JSON roundtrip makes JPEG integer table keys compare to the pinned JSON.
    _need(json.loads(_json(params))==asset['parameters'] and params['sampling'] in (0,1,2), 'CODEC_PARAMETERS_CHANGED')
    output=BytesIO()
    source.save(output,format='JPEG',qtables=params['qtables'],subsampling=params['sampling'],
        progressive=params['progressive'],optimize=False)
    _need(output.getvalue()==jpeg_raw,'REENCODED_BYTES_NOT_EXACT')
    cache[key]=_ExactEncoding(_SEAL,key)

def verify_media_ref(ref,facts,source,responses,*,cache):
    """Called by the internal real-capture consumer, never by a public flag."""
    raw=_read(ref);document=json.loads(raw);label=facts['target']['target_label']
    _need(document['schema_version']=='native-tiktok-deterministic-media-equivalence/v1'
        and document['binding']==_context(facts) and document['codec']==_codec(),'FROZEN_CONTEXT_OR_CODEC_CHANGED')
    target=document['targets'][label];assets=document['assets'];ordered=target['ordered']
    from domains.product_operations.approved_publication_snapshot import publication_images_for_target
    expected=list(publication_images_for_target(facts['snapshot'],label))
    _need(len(ordered)==len(expected)==7 and [row['position'] for row in ordered]==list(range(1,8))
        and len({row['official_uri'] for row in ordered})==7,'EXACT_SEVEN_ORDERED_POSITIONS_REQUIRED')
    binding=facts['plan']['payload']['r3_marketplace_binding'];r2=binding['r2_identity']
    approved=[row for name in ('generation_result','translation_result') for row in binding['documents'][name].get('assets',[])]
    pins=[(ref['path'],ref['sha256'])];seen=set()
    for url,row in zip(expected,ordered):
        index=row['asset_index'];_need(type(index) is int and 0<=index<len(assets),'ASSET_INDEX_INVALID')
        asset=assets[index];_need(asset['source_url']==url,'FROZEN_POSITION_SOURCE_CHANGED')
        matching=[item for item in approved if item.get('public_url')==url
            and item.get('artifact_digest')=='sha256:'+asset['source_ref']['sha256']
            and Path(item['artifact_path']).resolve()==Path(asset['source_ref']['path']).resolve()]
        _need(len(matching)==1 and 'sha256:'+asset['source_ref']['sha256'] in r2['artifact_digests'],
            'ORIGINAL_R2_ARTIFACT_MEMBERSHIP_REQUIRED')
        for key in ('source_ref','cdn_ref','cdn_response_ref'):
            item=asset[key];_read(item,image=key!='cdn_response_ref');pins.append((item['path'],item['sha256']))
        receipt=json.loads(_read(asset['cdn_response_ref']))
        _need(receipt['url']==asset['cdn_url'] and Path(receipt['local_path']).resolve()==Path(asset['cdn_ref']['path']).resolve()
            and receipt['byte_sha256']==asset['cdn_ref']['sha256'] and receipt['mime']=='image/jpeg','CDN_COLLECTION_REF_CHANGED')
        _need(index not in seen,'DUPLICATED_POSITION_ASSET');seen.add(index)
        _encode_once(asset,cache)
    _need(target['execution_identity_sha256']==_sha(source['identity']) and target['control_shop_id']==source['shop_id']
        and target['region']==source['region'],'ORIGINAL_EXECUTION_IDENTITY_CHANGED')
    pinned=json.loads(_read(target['official_detail_ref']));pins.append(tuple(target['official_detail_ref'][k] for k in ('path','sha256')))
    observed=pinned['identity']
    _need(all(observed.get(k)==v for k,v in {'target_label':label,'miaoshou_shop_id':source['shop_id'],
        'official_shop_id':target['official_shop_id'],'official_shop_region':source['region']}.items())
        and pinned['exact_product_id']==target['official_product_id'] and pinned['response']['code']==0,
        'ORIGINAL_SDK_RESPONSE_REF_CHANGED')
    _media(pinned['response']['data'],ordered,assets)
    summary={'kind':'DETERMINISTIC_REPRESENTATION_EQUIVALENCE','ref':ref,'target_label':label,
        'ordered_mapping':[{'position':row['position'],'official_uri':row['official_uri'],
            'source_sha256':assets[row['asset_index']]['source_ref']['sha256'],
            'jpeg_sha256':assets[row['asset_index']]['cdn_ref']['sha256']} for row in ordered],
        'codec':document['codec'],'exact_compressed_bytes':True,'historical_upload_origin_proven':False,
        'description_object_uri_order_verified':True,'description_rendition_bytes_verified':False}
    proof=_VerifiedMedia(_SEAL,_sha(_context(facts)),_sha(source['identity']),label,_json(document),tuple(sorted(set(pins))),_json(summary))
    recheck_media(proof,facts,source,responses)
    return proof

def recheck_media(proof,facts,source,responses):
    _need(type(proof) is _VerifiedMedia and proof.seal is _SEAL,'INTERNAL_VERIFIED_OBJECT_REQUIRED')
    _need(proof.context_sha256==_sha(_context(facts)) and proof.identity_sha256==_sha(source['identity'])
        and proof.label==facts['target']['target_label'],'CURRENT_FROZEN_CONTEXT_CHANGED')
    for path,digest in proof.pins:_read({'path':path,'sha256':digest},image=Path(path).suffix.lower() in ('.png','.jpg','.jpeg'))
    document=json.loads(proof.document_json);target=document['targets'][proof.label]
    _need(document['codec']==_codec(),'CURRENT_CODEC_CHANGED')
    # These are the current real SDK response objects, then the identical
    # immutable read set during the existing protected short CAS replay.
    _need(len(responses)==5,'CURRENT_SDK_READ_SET_REQUIRED')
    from shared_platform.native_tiktok_exact_readback import _official_identity
    shop=_official_identity(responses[0]['response'],responses[1]['response'],
        internal_id=source['shop_id'],region=source['region'])
    _need(str(shop.get('id') or shop.get('shop_id'))==target['official_shop_id'],'CURRENT_OFFICIAL_SHOP_CHANGED')
    pinned=json.loads(_read(target['official_detail_ref']))
    cipher=str(shop.get('cipher') or shop.get('shop_cipher') or '')
    _need(pinned['identity'].get('cipher_sha256')==sha256(cipher.encode()).hexdigest(),'ORIGINAL_SDK_SHOP_CIPHER_CHANGED')
    detail=responses[3]['response']['data']
    _need(str(detail.get('id') or detail.get('product_id'))==target['official_product_id'],'CURRENT_OFFICIAL_PRODUCT_CHANGED')
    _media(detail,target['ordered'],document['assets'])

def normalized_media(proof,detail,command,shop):
    _need(type(proof) is _VerifiedMedia and proof.seal is _SEAL and command['target_label']==proof.label,
        'INTERNAL_VERIFIED_OBJECT_REQUIRED')
    document=json.loads(proof.document_json);target=document['targets'][proof.label]
    _need(str(shop.get('id') or shop.get('shop_id'))==target['official_shop_id']
        and command['control']['shop_id']==target['control_shop_id']
        and str(detail.get('id') or detail.get('product_id'))==target['official_product_id'],'CURRENT_READER_IDENTITY_CHANGED')
    ordered=target['ordered'];assets=document['assets'];_media(detail,ordered,assets)
    urls=[assets[row['asset_index']]['source_url'] for row in ordered]
    _need(urls==command['publisher_snapshot']['targets'][0]['expected_images'],'CURRENT_COMMAND_MEDIA_CHANGED')
    from shared_platform.product_description_media import miaoshou_rich_description,tiktok_description_text
    value=deepcopy(detail);key='main_images' if value.get('main_images') else 'images'
    for image,url in zip(value[key],urls):image['urls']=[url];image.pop('url_list',None)
    text=tiktok_description_text(detail['description'])
    value['description']=miaoshou_rich_description(text,urls)
    _need(tiktok_description_text(value['description'])==text,'DESCRIPTION_TEXT_CHANGED')
    return value,json.loads(proof.summary_json)
