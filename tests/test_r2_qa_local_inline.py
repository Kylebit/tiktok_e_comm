import base64
import hashlib
import importlib.util
from pathlib import Path

import pytest
from PIL import Image


def module():
    path=Path(__file__).parents[1]/'skills/prepare-product-images/scripts/run_automated_image_qa.py'
    spec=importlib.util.spec_from_file_location('qa_inline_test',path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def test_inline_sends_exact_reviewed_bytes_even_when_public_url_is_a_different_delivery(tmp_path):
    path=tmp_path/'reviewed.png';Image.new('RGB',(32,32),'red').save(path)
    raw=path.read_bytes();row={'artifact_path':str(path),'artifact_digest':'sha256:'+hashlib.sha256(raw).hexdigest(),'public_url':'https://example.invalid/downsampled.jpg'}
    url,size=module()._qa_image_reference(row,verified_local=True)
    assert url.startswith('data:image/png;base64,')
    assert base64.b64decode(url.split(',',1)[1])==raw and size==len(raw)
    assert module()._qa_image_reference(row)==(row['public_url'],0)


def test_changed_local_bytes_cannot_be_sent_as_the_old_reviewed_digest(tmp_path):
    path=tmp_path/'reviewed.png';Image.new('RGB',(32,32),'red').save(path)
    row={'artifact_path':str(path),'artifact_digest':'sha256:'+hashlib.sha256(path.read_bytes()).hexdigest()}
    Image.new('RGB',(32,32),'blue').save(path)
    with pytest.raises(ValueError,match='digest changed'):module()._qa_image_reference(row,verified_local=True)
