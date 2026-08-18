from types import SimpleNamespace

import pytest

from domains.channel_operations.oneclick_write_occurrences import (
    WriteOccurrenceRecordingError,
    WriteOccurrenceState,
)


def _request(recorder, *, target_label="shopee:GLOBAL"):
    return SimpleNamespace(
        job_id="oneclick-job:fixture",
        target_label=target_label,
        progress_recorder=recorder,
    )


def test_occurrence_open_precedes_network_and_confirm_is_exact():
    calls = []

    def recorder(*args):
        calls.append(args)

    request = _request(recorder)
    state = WriteOccurrenceState()
    occurrence = state.open(
        request,
        occurrence_id="image_upload-1",
        write_class="shopee:image:upload",
        evidence={"position": 1},
    )
    assert calls[-1][7] == "PRE_INVOCATION_INTENT"
    assert calls[-1][4:7] == (None, 0, 1)

    state.confirm(request, occurrence, evidence={"accepted": True})

    assert calls[-1][7] == "POST_RESPONSE_CONFIRMED"
    assert calls[-1][4:7] == (1, 1, 1)
    assert state.external_writes == ("shopee:image:upload",)
    assert state.external_write_count == 1


def test_occurrence_explicit_rejection_keeps_prior_exact_count():
    calls = []
    request = _request(lambda *args: calls.append(args))
    state = WriteOccurrenceState()
    occurrence = state.open(
        request,
        occurrence_id="global_create-1",
        write_class="shopee:global_master:create",
        evidence={"intent": True},
    )

    state.reject(request, occurrence, evidence={"write_applied": False})

    assert calls[-1][7] == "POST_RESPONSE_REJECTED"
    assert calls[-1][4:7] == (0, 0, 0)
    assert state.external_writes == ()
    assert state.external_write_count == 0


def test_open_recorder_failure_proves_network_was_not_invoked():
    def recorder(*_args):
        raise OSError("sqlite unavailable")

    state = WriteOccurrenceState()
    with pytest.raises(WriteOccurrenceRecordingError) as error:
        state.open(
            _request(recorder),
            occurrence_id="regional_publish-1",
            write_class="shopee:regional_publish",
            evidence={"intent": True},
        )

    assert error.value.network_invoked is False
    assert error.value.external_write_count == 0
    assert error.value.confirmed_lower_bound == 0
    assert error.value.possible_upper_bound == 0


@pytest.mark.parametrize(
    ("resolution", "expected_count"),
    [("confirm", 1), ("reject", 0)],
)
def test_resolution_recorder_failure_preserves_truthful_exact_bound(
    resolution, expected_count
):
    call_count = 0

    def recorder(*_args):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("receipt transaction unavailable")

    request = _request(recorder)
    state = WriteOccurrenceState()
    occurrence = state.open(
        request,
        occurrence_id="detail_update-1",
        write_class="miaoshou:tiktok_detail:update",
        evidence={"intent": True},
    )

    with pytest.raises(WriteOccurrenceRecordingError) as error:
        getattr(state, resolution)(
            request, occurrence, evidence={"terminal": True}
        )

    assert error.value.network_invoked is True
    assert error.value.external_write_count == expected_count
    assert error.value.confirmed_lower_bound == expected_count
    assert error.value.possible_upper_bound == expected_count


def test_transport_unknown_leaves_open_interval_without_retry_claim():
    state = WriteOccurrenceState()
    occurrence = state.open(
        _request(lambda *_args: None),
        occurrence_id="publish_submit-1",
        write_class="miaoshou:tiktok_publish:submit",
        evidence={"intent": True},
    )

    writes, exact, lower, upper = state.unknown_bounds(occurrence)

    assert writes == ("miaoshou:tiktok_publish:submit",)
    assert exact is None
    assert (lower, upper) == (0, 1)


def test_storefront_progress_counts_only_confirmed_response():
    calls = []
    request = _request(
        lambda *args: calls.append(args), target_label="tiktok:MX"
    )
    state = WriteOccurrenceState()
    occurrence = state.open(
        request,
        occurrence_id="detail_update-1",
        write_class="miaoshou:tiktok_detail:update",
        evidence={"intent": True},
    )
    assert calls == []

    state.confirm(request, occurrence, evidence={"accepted": True})

    assert len(calls) == 1
    assert calls[0][4:8] == (1, 1, 1, None)
