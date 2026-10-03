"""Opt-in mutation controls: pytest -p q01_finance_faults with targeted nodes.

Wrap the actual captured consumers in memory; no source or fixture files mutate.
Each selected existing behavioral test must fail. Normal pytest never loads this.
"""
import pytest
from domains.data_operations.profit_settlement import captured_samples,captured_waterfall,captured_sku

def pytest_runtest_setup(item):
    patch=pytest.MonkeyPatch();item._q01_patch=patch
    name=item.name
    original=captured_samples.build_sample_audit
    def samples(*args,**kwargs):
        if name.startswith('test_optional_sample_fees_omitted') and 'sample_fees' not in args[0]['waterfall']:
            raise KeyError('sample_fees')
        result=original(*args,**kwargs)
        if name.startswith('test_all_37_samples'):result['rows']=result['rows'][:10]
        elif name.startswith('test_missing_model_inputs'):
            for row in result['rows']:
                if row['profit_cny'] is None:row.update(profit_cny=0,profit_class='zero')
        elif name.startswith('test_sign_classes'):
            for row in result['rows']:
                if row['affiliate_state']=='unknown':row['affiliate_state']='without'
        return result
    patch.setattr(captured_samples,'build_sample_audit',samples)
    if name.startswith('test_full_waterfall'):
        original_waterfall=captured_waterfall.build_waterfall
        def double_fee(*args,**kwargs):
            result=original_waterfall(*args,**kwargs)
            # Re-deduct a fee already included in platform net settlement.
            result['posterior']['all']['estimate']['profit_cny']-=float(result['observed']['components']['commission_local']['amount'])
            return result
        patch.setattr(captured_waterfall,'build_waterfall',double_fee)
    if name.startswith('test_cross_scope_and_suffix'):
        original_sku=captured_sku.build_captured_sku
        def ignore_identity(profile,identity=None):
            if identity is not None:identity=original_sku(profile)['choices'][0]['identity']
            return original_sku(profile,identity)
        patch.setattr(captured_sku,'build_captured_sku',ignore_identity)

def pytest_runtest_teardown(item):
    item._q01_patch.undo()
