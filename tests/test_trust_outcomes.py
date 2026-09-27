"""Every result code in spec section 1 has a sentence, and no sentence contains a
code, an id, or an underscore (spec 1.25; acceptance test 37).
"""

from __future__ import annotations

from petasos.trust.outcomes import SENTENCES, make_result

_ALL_RESULT_CODES = {
    "staged",
    "approved",
    "executed",
    "already_executed",
    "aborted",
    "refused/unknown_tool",
    "refused/hard_constraint",
    "refused/invalid_arguments",
    "refused/rail_full",
    "refused/not_an_approver",
    "refused/self_approval",
    "refused/verb_mismatch",
    "refused/unknown_or_used_or_expired",
    "refused/not_approved",
    "refused/record_mismatch",
    "refused/stale_policy",
    "refused/stale_resource",
    "failed/execution_error",
}


def test_every_result_code_has_a_sentence() -> None:
    assert set(SENTENCES) == _ALL_RESULT_CODES


def test_no_sentence_contains_a_code_or_underscore() -> None:
    for code, sentence in SENTENCES.items():
        assert "_" not in sentence, f"{code!r} sentence contains an underscore: {sentence!r}"
        assert "/" not in sentence, f"{code!r} sentence contains a slash: {sentence!r}"
        assert code not in sentence, f"{code!r} appears verbatim in its own sentence"


def test_make_result_fills_in_the_sentence() -> None:
    result = make_result("staged")
    assert result.code == "staged"
    assert result.sentence == SENTENCES["staged"]
