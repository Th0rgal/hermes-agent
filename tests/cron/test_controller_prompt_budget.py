"""Job prompt budget must not count inlined skills."""

import pytest

from cron.controller_scope import (
    CONTROLLER_PROMPT_MAX_CHARS,
    ControllerScope,
    ControllerScopeError,
    check_prompt_budget,
)


def _scope() -> ControllerScope:
    return ControllerScope(
        job_id="08d84a9565f1",
        project="verity-lido",
        repositories=("lfglabs-dev/lido-srv3-proof-closure",),
        permissions=("sandboxed.read", "sandboxed.mutate"),
        reserved_local_areas=(),
        aliases=(),
    )


def test_budget_allows_assembled_prompt_when_job_prompt_fits():
    scope = _scope()
    job_prompt = "Controller of project verity-lido.\n" * 20
    assembled = "# controllers-policy\n" + ("x" * 30_000) + "\n" + job_prompt
    assert len(job_prompt) < CONTROLLER_PROMPT_MAX_CHARS
    assert len(assembled) > CONTROLLER_PROMPT_MAX_CHARS
    assert check_prompt_budget(scope, assembled, measured=job_prompt) == assembled


def test_budget_rejects_oversized_job_prompt():
    scope = _scope()
    huge = "y" * (CONTROLLER_PROMPT_MAX_CHARS + 1)
    with pytest.raises(ControllerScopeError, match="job prompt"):
        check_prompt_budget(scope, huge, measured=huge)


def test_budget_without_measured_still_counts_the_passed_text():
    scope = _scope()
    assembled = "z" * (CONTROLLER_PROMPT_MAX_CHARS + 1)
    with pytest.raises(ControllerScopeError):
        check_prompt_budget(scope, assembled)


def test_budget_skipped_when_not_a_controller():
    huge = "z" * (CONTROLLER_PROMPT_MAX_CHARS + 1)
    assert check_prompt_budget(None, huge) == huge
