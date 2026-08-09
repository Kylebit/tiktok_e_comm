import pytest

from domains.supply_chain_operations.transport_history import derive_transport_policy


def test_th_history_raises_transport_to_conservative_p80():
    policy = derive_transport_policy(
        [12, 15, 19, 22, 24, 24, 24, 27, 30],
        baseline_transport_days=15,
    )

    assert policy.eligible_samples == 9
    assert policy.p80_total_days == 27
    assert policy.derived_transport_days == 20
    assert policy.effective_transport_days == 20
    assert policy.state == "HISTORICAL_P80_UPLIFT"


def test_history_never_shortens_approved_baseline():
    policy = derive_transport_policy(
        [8, 8, 9, 12, 21],
        baseline_transport_days=15,
    )

    assert policy.p80_total_days == 12
    assert policy.derived_transport_days == 5
    assert policy.effective_transport_days == 15
    assert policy.state == "BASELINE_FLOOR"


def test_small_sample_falls_back_to_baseline_even_when_observed_is_slower():
    policy = derive_transport_policy(
        [24, 33],
        baseline_transport_days=25,
    )

    assert policy.derived_transport_days == 26
    assert policy.effective_transport_days == 25
    assert policy.state == "FALLBACK_INSUFFICIENT_SAMPLE"


@pytest.mark.parametrize("invalid", [True, 2.5, "12", 0, -1])
def test_history_rejects_invalid_day_values(invalid):
    with pytest.raises(TypeError):
        derive_transport_policy([invalid], baseline_transport_days=15)
