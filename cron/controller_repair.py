"""Reviewable existing-controller repair: python -m cron.controller_repair --help."""
import argparse
import json
from pathlib import Path

from cron import jobs
from cron.controller_scope import (
    CONTROLLER_PROMPT_MAX_CHARS, ControllerScopeError, bind_controller_scope, validate_controller_job,
)


def _validate_prompt(job, legacy_prompt_only):
    if not legacy_prompt_only:
        if job.get("controller") is None:
            raise ValueError("Existing controller job required; explicitly export with --legacy-prompt-only")
        validate_controller_job(job)
        return
    if job.get("controller") is not None:
        raise ValueError("Legacy prompt-only repair requires absent/null controller metadata")
    from cron.scheduler import _build_job_prompt
    with bind_controller_scope(None):
        assembled = _build_job_prompt(job, validation_only=True)
    if len(assembled) > CONTROLLER_PROMPT_MAX_CHARS:
        raise ControllerScopeError(
            f"Legacy repair assembled prompt is {len(assembled)} chars; maximum is "
            f"{CONTROLLER_PROMPT_MAX_CHARS}. Review the full prompt; enrollment is unchanged."
        )


def export_repair(job_id, *, legacy_prompt_only=False):
    original = jobs.get_job(job_id)
    if not original or (not legacy_prompt_only and not original.get("controller")):
        raise ValueError("Existing controller job required")
    diagnostic = None
    try:
        _validate_prompt(original, legacy_prompt_only)
    except ControllerScopeError as exc:
        diagnostic = str(exc)
    return {"original": original, "diagnostic": diagnostic,
            "legacy_prompt_only": legacy_prompt_only,
            "replacement_prompt": original["prompt"]}


def apply_repair(document):
    original = document["original"]
    prompt = document["replacement_prompt"]
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("A reviewed nonempty replacement prompt is required")
    _validate_prompt({**original, "prompt": prompt}, document.get("legacy_prompt_only") is True)
    # Atomic compare with the entire snapshot under the existing jobs lock.
    # Neither scope, tools, skills nor paused/runnable state changes here.
    result = jobs.update_job(original["id"], {"prompt": prompt}, expected_job=original)
    if result is None:
        raise ValueError("Job no longer exists")
    return result


def main():
    parser = argparse.ArgumentParser(description="Export an existing controller for owner-reviewed prompt repair; apply refuses stale snapshots.")
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("job_id")
    export.add_argument("file", type=Path)
    export.add_argument("--legacy-prompt-only", action="store_true",
                        help="Review a legacy prompt against 16K without enrolling or changing authority")
    apply = sub.add_parser("apply")
    apply.add_argument("file", type=Path)
    args = parser.parse_args()
    if args.command == "export":
        # Do not overwrite another reviewed proposal.
        with args.file.open("x", encoding="utf-8") as handle:
            json.dump(export_repair(args.job_id, legacy_prompt_only=args.legacy_prompt_only), handle, indent=2)
    else:
        result = apply_repair(json.loads(args.file.read_text(encoding="utf-8")))
        print(json.dumps({"id": result["id"], "state": result.get("state")}))


if __name__ == "__main__":
    main()
