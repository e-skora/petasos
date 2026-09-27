"""Result code -> one plain sentence. Every `Result` carries `code` and `sentence`, so
nothing that reaches a screen is ever a bare code.
"""

from __future__ import annotations

from dataclasses import dataclass

from petasos.trust.risk import Tier

SENTENCES: dict[str, str] = {
    "staged": "This action needs a person to say yes before anything happens.",
    "approved": "A person said yes, and this is waiting to run.",
    "executed": "This ran, and the result was recorded.",
    "already_executed": "This already ran once, so nothing happened again.",
    "aborted": "This was called off before it could run.",
    "refused/unknown_tool": "There is no such action, so nothing happened.",
    "refused/hard_constraint": "This action is never allowed, no matter who asks.",
    "refused/invalid_arguments": (
        "The details given for this action did not make sense, so nothing happened."
    ),
    "refused/rail_full": (
        "Too many actions of this kind already happened in the last hour, so this one waits."
    ),
    "refused/not_an_approver": "Only a person allowed to approve can do this.",
    "refused/self_approval": "A person cannot approve their own request.",
    "refused/verb_mismatch": "That approval was for a different action, so it was thrown away.",
    "refused/unknown_or_used_or_expired": "That approval is no longer valid.",
    "refused/not_approved": "This action has not been approved yet, so it cannot run.",
    "refused/record_mismatch": (
        "The details of this action changed since it was approved, so it will not run."
    ),
    "refused/stale_policy": (
        "The rules changed since this action was approved, so it will not run under the old rules."
    ),
    "refused/stale_resource": (
        "What this action was about to change has since changed, so it will not run."
    ),
    "failed/execution_error": (
        "Something went wrong while running this action, and nothing was changed."
    ),
}


@dataclass(frozen=True)
class Result:
    code: str
    sentence: str
    tier: Tier | None = None
    verb: str | None = None
    grant_id: int | None = None
    scope: str | None = None


def make_result(
    code: str,
    *,
    tier: Tier | None = None,
    verb: str | None = None,
    grant_id: int | None = None,
    scope: str | None = None,
) -> Result:
    return Result(
        code=code, sentence=SENTENCES[code], tier=tier, verb=verb, grant_id=grant_id, scope=scope
    )
