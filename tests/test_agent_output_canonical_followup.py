"""Metadata-only checks for canonical output argv and scoped bootstrap guidance."""
from pathlib import Path

import pytest
from test_agent_entry_binding import ROOT, synthetic
from test_agent_r1_path_binding import v2


@pytest.mark.parametrize('fixture_name', ['synthetic', 'v2'])
@pytest.mark.parametrize('spelling', ['--o', '--out', '--outp=', '--outpu='])
def test_output_abbreviations_are_rejected_before_domain_import(request, fixture_name, spelling):
    root, profile, invoke = request.getfixturevalue(fixture_name)
    output = str(Path(profile['output_root'])/'packet.json')
    arguments = [spelling+output] if spelling.endswith('=') else [spelling, output]
    result = invoke(arguments=arguments)
    assert result.returncode == 2
    assert 'ENTRY_ROOT_ARGUMENT_UNSUPPORTED' in result.stderr
    assert 'DOMAIN_IMPORT_FORBIDDEN' not in result.stdout+result.stderr
    assert not Path(output).exists()


@pytest.mark.parametrize('fixture_name', ['synthetic', 'v2'])
@pytest.mark.parametrize('equals', [False, True])
def test_canonical_output_remains_valid_metadata(request, fixture_name, equals):
    root, profile, invoke = request.getfixturevalue(fixture_name)
    output = str(Path(profile['output_root'])/'packet.json')
    arguments = ['--output='+output] if equals else ['--output', output]
    result = invoke(arguments=arguments)
    assert result.returncode == 0, result.stderr
    assert 'DOMAIN_IMPORT_FORBIDDEN' not in result.stdout+result.stderr
    assert not Path(output).exists()


def test_bootstrap_paragraph_explicitly_limits_original_contract():
    text = (ROOT/'skills/prepare-product-publication/SKILL.md').read_text(encoding='utf-8')
    paragraphs = text.split('\n\n')
    paragraph = next(p for p in paragraphs if 'local workbench-state write once' in p)
    assert 'Version 1' in paragraph and 'original entry' in paragraph
    assert 'Version 2' in paragraph and 'R1_CAPTURED_STATE_MISSING' in paragraph
    assert 'without reading upstream or creating state' in paragraph


def test_r1_parser_and_agent_commands_keep_full_flags():
    script = (ROOT/'skills/prepare-product-publication/scripts/prepare_product_publication.py').read_text(encoding='utf-8')
    skill = (ROOT/'skills/prepare-product-publication/SKILL.md').read_text(encoding='utf-8')
    assert 'allow_abbrev=False' in script
    assert '<SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_PROFILE>' in skill
    assert '--entry preparation -- --offer-id <OFFER_ID> --targets <COMMA_SEPARATED_TARGETS>' in skill
