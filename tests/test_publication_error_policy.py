from shared_platform.publication_error_policy import classify_publication_error


def test_unknown_write_never_retries_and_requires_readback_reconciliation():
    result = classify_publication_error(
        stage="DISPATCH",
        reason_category="POST_WRITE",
        provider_code="timeout",
        external_write_count=None,
        outcome_unknown=True,
    )

    assert result["class"] == "UNKNOWN_WRITE"
    assert result["automatic_retry_attempts"] == 0
    assert result["readback_reconciliation_attempts"] == 3
    assert result["next_action"] == "RECONCILE_BEFORE_ANY_RETRY"


def test_local_validation_gets_one_repair_but_no_blind_retry():
    result = classify_publication_error(
        stage="PREPARE",
        reason_category="CONTENT",
        provider_code="required_attribute_missing",
        external_write_count=0,
    )

    assert result["class"] == "LOCAL_VALIDATION"
    assert result["automatic_repair_attempts"] == 1
    assert result["automatic_retry_attempts"] == 0


def test_transient_zero_write_retry_is_bounded():
    result = classify_publication_error(
        stage="PREPARE",
        reason_category="DEPENDENCY",
        provider_code="rate_limit_temporarily_unavailable",
        external_write_count=0,
    )

    assert result["class"] == "TRANSIENT_ZERO_WRITE"
    assert result["automatic_retry_attempts"] == 2
