"""Three bounded local regressions for the explicit identity-correction seam."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from shared_platform.sku_only_publication_correction import (
    POINTER, SCHEMA, _digest, preparation_documents, replace_sku_identity,
    report_directory,
)


class SkuOnlyCorrectionTests(unittest.TestCase):
    def test_identity_delta_preserves_copy_source_receipts_and_variant_keys(self):
        old = {'seller_sku': '0990', 'title': '0990 remains literal supplier copy',
            'skus': [{'source_key': '0990', 'model_sku': '0990', 'price': 13}],
            'category_evidence_binding': {'receipt': {'seller_sku': '0990'}},
            'image': {'artifact_digest': 'sha256:0990', 'brand': 'HomeBloom'}}
        retained = deepcopy(old)
        result = replace_sku_identity(old, '0990', '9457')
        self.assertEqual(result['seller_sku'], '9457')
        self.assertEqual(result['skus'][0]['model_sku'], '9457')
        self.assertEqual(result['skus'][0]['source_key'], '0990')
        self.assertEqual(result['title'], old['title'])
        self.assertEqual(result['category_evidence_binding'], old['category_evidence_binding'])
        self.assertEqual(result['image'], old['image'])
        self.assertEqual(old, retained)

    def test_new_report_generation_keeps_old_namespace_and_rejects_tamper(self):
        with tempfile.TemporaryDirectory() as supplied:
            root = Path(supplied)
            base = root / '4106902637'
            base.mkdir()
            old = base / 'first-review.json'
            old.write_bytes(b'original frozen input')
            generation = 'a' * 32
            directory = base / 'sku-corrections' / generation
            directory.mkdir(parents=True)
            record = {'schema_version': SCHEMA, 'offer_id': '4106902637',
                      'generation_id': generation}
            record['correction_digest'] = _digest(record)
            pointer = base / POINTER
            pointer.write_text(json.dumps(record), encoding='utf-8')
            self.assertEqual(report_directory(root, '4106902637'), directory)
            self.assertEqual(old.read_bytes(), b'original frozen input')
            record['generation_id'] = '../another-offer'
            pointer.write_text(json.dumps(record), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'REPORT_POINTER_INVALID'):
                report_directory(root, '4106902637')

    def test_new_r1_keeps_commercial_facts_and_retains_original_provenance(self):
        first = {'product_center_revision': 4, 'product_facts': {
            'seller_sku': '0990', 'purchase_cost_cny': 13, 'weight_kg': 0.3,
            'package_cm': [15, 13, 10], 'skus': [{'source_key': 'one', 'seller_sku': '0990'}]},
            'targets': [{'target': 'tiktok:HB_MY', 'discount': 35, 'stock': 200},
                        {'target': 'shopee:MY', 'discount': 30, 'stock': 200}],
            'category_evidence_binding': {'receipt': {'seller_sku': '0990'}}}
        snapshot = {'snapshot_digest': 'old-digest', 'product_approval_fingerprint': 'old-fingerprint',
            'fact_snapshot': deepcopy(first), 'human_approval': False}
        documents = {'first_review': first, 'round1_snapshot': snapshot}
        original = deepcopy(documents)
        record = {'offer_id': '4106902637', 'old_sku': '0990', 'new_sku': '9457',
            'generation_id': 'b' * 32, 'correction_digest': 'sha256:' + 'c' * 64,
            'created_at': '2026-11-03T00:00:00+00:00', 'old_snapshot_digest': 'old-digest',
            'original_prepared_reference': 'r1-prepared:original'}
        new_first, new_r1 = preparation_documents(record, documents, current_revision=6)
        self.assertEqual(new_first['product_center_revision'], 4)
        self.assertEqual(new_r1['approved_product_center_revision'], 7)
        self.assertFalse(new_r1['human_approval'])
        self.assertEqual(new_first['targets'], first['targets'])
        self.assertEqual(new_first['category_evidence_binding'], first['category_evidence_binding'])
        expected = replace_sku_identity(first, '0990', '9457')
        self.assertEqual(new_first, expected)
        unsigned = dict(new_r1)
        supplied = unsigned.pop('snapshot_digest')
        self.assertEqual(supplied, _digest(unsigned))
        self.assertEqual(documents, original)


if __name__ == '__main__':
    unittest.main()
