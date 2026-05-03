"""``soniq dead-letter`` - operate on dead-letter queue jobs."""

from __future__ import annotations

import os
import sys

from soniq.features.dead_letter import DeadLetterFilter

from ._context import cli_app
from ._helpers import database_url_argument

# Bulk replay/delete prompts above this threshold unless --yes is passed.
# Below it, single-digit operations are treated as deliberate and run silently.
_BULK_CONFIRM_THRESHOLD = 5


def add_dead_letter_cmd(subparsers) -> None:
    parser = subparsers.add_parser(
        "dead-letter",
        help="Manage dead letter queue jobs",
        description="Manage jobs in the dead letter queue",
    )
    parser.add_argument(
        "action",
        choices=["list", "replay", "delete", "cleanup", "export"],
        help="Action to perform",
    )
    parser.add_argument("job_ids", nargs="*", help="Job IDs (for replay)")
    parser.add_argument("--all", action="store_true", help="Apply action to all jobs")
    parser.add_argument("--filter", help="Filter by job name pattern")
    parser.add_argument("--limit", type=int, default=50, help="Maximum jobs to show")
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Remove jobs older than N days",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Report what would happen without making changes. Honoured for "
            "``replay --all`` and ``delete --all``; ``cleanup`` ignores it."
        ),
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the interactive confirmation prompt for bulk operations.",
    )
    parser.add_argument(
        "--format",
        choices=["csv", "json"],
        default="csv",
        help="Export format",
    )
    parser.add_argument("--output", help="Output file path")
    database_url_argument(parser)
    parser.set_defaults(func=handle_dead_letter)


def _confirm(prompt: str) -> bool:
    """Interactive [y/N] prompt. Defaults to no on EOF or non-tty."""
    if not sys.stdin.isatty():
        # Non-interactive: refuse rather than guessing. The caller should
        # pass --yes if they want to skip the prompt.
        print(
            "Refusing to run a bulk dead-letter operation in a non-interactive "
            "shell without --yes. Re-run with --yes to confirm.",
            file=sys.stderr,
        )
        return False
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


async def handle_dead_letter(args) -> int:
    async with cli_app(args) as app:
        dead_letter = app.dead_letter

        action = args.action
        filter_criteria = DeadLetterFilter()
        filter_criteria.limit = args.limit
        if args.filter:
            filter_criteria.job_names = [args.filter]

        if action == "list":
            jobs = await dead_letter.list_dead_letter_jobs(filter_criteria)
            for job in jobs:
                print(f"{job.id}  {job.job_name}  {job.dead_letter_reason}")
            return 0

        if action == "replay":
            if args.all:
                jobs = await dead_letter.list_dead_letter_jobs(filter_criteria)
                count = len(jobs)
                if count == 0:
                    print("No dead-letter jobs match the filter.")
                    return 0

                if args.dry_run:
                    print(f"Dry run: would replay {count} dead-letter job(s).")
                    for job in jobs[:10]:
                        print(f"  {job.id}  {job.job_name}  {job.dead_letter_reason}")
                    if count > 10:
                        print(f"  ... and {count - 10} more")
                    return 0

                if not args.yes and count >= _BULK_CONFIRM_THRESHOLD:
                    prompt = (
                        f"This will re-queue {count} dead-letter job(s). If the "
                        "underlying issue has not been fixed, they will fail "
                        "again and return to the dead-letter queue. Continue? [y/N] "
                    )
                    if not _confirm(prompt):
                        print("Aborted.")
                        return 1

                replayed = await dead_letter.bulk_replay(filter_criteria)
                print(f"Replayed {len(replayed)} of {count} dead-letter job(s).")
                return 0
            if args.job_ids:
                for job_id in args.job_ids:
                    await dead_letter.replay(job_id)
                return 0
            return 1

        if action == "cleanup":
            # ``cleanup_old_dead_letter_jobs`` does not honour ``--dry-run``;
            # the flag stays on the parser for symmetry but is a no-op here.
            removed = await dead_letter.cleanup_old_dead_letter_jobs(days=args.days)
            print(f"Removed {removed} dead-letter job(s) older than {args.days} days.")
            return 0

        if action == "delete":
            if args.all:
                jobs = await dead_letter.list_dead_letter_jobs(filter_criteria)
                count = len(jobs)
                if count == 0:
                    print("No dead-letter jobs match the filter.")
                    return 0

                if args.dry_run:
                    print(f"Dry run: would delete {count} dead-letter job(s).")
                    for job in jobs[:10]:
                        print(f"  {job.id}  {job.job_name}  {job.dead_letter_reason}")
                    if count > 10:
                        print(f"  ... and {count - 10} more")
                    return 0

                if not args.yes and count >= _BULK_CONFIRM_THRESHOLD:
                    prompt = (
                        f"This will permanently delete {count} dead-letter "
                        "job(s). This cannot be undone. Continue? [y/N] "
                    )
                    if not _confirm(prompt):
                        print("Aborted.")
                        return 1

                deleted = await dead_letter.bulk_delete(filter_criteria)
                print(f"Deleted {deleted} dead-letter job(s).")
                return 0
            if args.job_ids:
                for job_id in args.job_ids:
                    await dead_letter.delete_dead_letter_job(job_id)
                return 0
            return 1

        if action == "export":
            if not args.output:
                print("--output is required for export")
                return 1
            filename = await dead_letter.export_dead_letter_jobs(
                filter_criteria, format=args.format
            )
            if args.output and args.output != filename:
                os.replace(filename, args.output)
                filename = args.output
            print(filename)
            return 0

        print("Unknown action")
        return 1
