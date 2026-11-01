"""Read-only image display before the complete marketplace candidate exists.

The caller must obtain documents through the original service-owned R2 reader.
This projection grants no content approval, COMMON write or publication action.
"""
from collections import OrderedDict

from shared_platform.publication_gallery_contract import validate_gallery_documents
from shared_platform.publication_r3_image_bridge import (
    _canonical_digest, build_dual_brand_publication_bridge, validate_r2_identity,
)


def project_prepared_images(documents):
    identity = validate_r2_identity(documents)
    first, generation = documents['first_review'], documents['generation_result']
    route = build_dual_brand_publication_bridge(
        offer_id=identity['offer_id'], first_review=first, generation=generation,
        translation=documents['translation_result'],
        approved_by=documents['round1_snapshot']['approved_by'])
    validate_gallery_documents(documents, route)
    groups = OrderedDict()
    for asset in generation['assets']:
        brand = asset['brand_id']
        group = groups.setdefault(brand, {
            'brand_id': brand, 'brand_label': asset.get('brand_label') or brand,
            'images': []})
        group['images'].append({
            'position': len(group['images']) + 1,
            'review_number': asset['review_number'], 'role': asset['role'],
            'url': asset['public_url'], 'artifact_id': asset.get('artifact_id'),
            'artifact_digest': asset['artifact_digest']})
    value = {
        'schema_version': 'native-prepared-image-review/v1',
        'offer_id': identity['offer_id'],
        'status': 'TECHNICALLY_PREPARED', 'qa_status': 'PASSED',
        'execution_authority': False, 'publication_approval_recorded': False,
        'round1_snapshot_digest': identity['round1_snapshot_digest'],
        'r2_identity_digest': identity['identity_digest'],
        'qa_digest': identity['qa_digest'],
        'target_labels': list(route['targets']),
        'image_count': len(generation['assets']),
        'brand_groups': list(groups.values()),
        'localized_image_count': len(documents['translation_result']['assets']),
    }
    return {**value, 'projection_digest': _canonical_digest(value)}
