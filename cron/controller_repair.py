"""Reviewable existing-controller repair: python -m cron.controller_repair --help."""
import argparse
import json
from pathlib import Path

from cron import jobs
from cron.controller_scope import ControllerScopeError, validate_controller_job


def export_repair(job_id):
    original = jobs.get_job(job_id)
    if not original or not original.get("controller"):
        raise ValueError("Existing controller job required")
    diagnostic = None
    try:
        validate_controller_job(original)
    except ControllerScopeError as exc:
        diagnostic = str(exc)
    return {"original": original, "diagnostic": diagnostic,
            "replacement_prompt": original["prompt"]}


def apply_repair(document):
    original = document["original"]
    prompt = document["replacement_prompt"]
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("A reviewed nonempty replacement prompt is required")
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
    apply = sub.add_parser("apply")
    apply.add_argument("file", type=Path)
    args = parser.parse_args()
    if args.command == "export":
        # Do not overwrite another reviewed proposal.
        with args.file.open("x", encoding="utf-8") as handle:
            json.dump(export_repair(args.job_id), handle, indent=2)
    else:
        result = apply_repair(json.loads(args.file.read_text(encoding="utf-8")))
        print(json.dumps({"id": result["id"], "state": result.get("state")}))


if __name__ == "__main__":
    main()
