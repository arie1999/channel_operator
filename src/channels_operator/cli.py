"""Command-line interface for manual pipeline runs and admin tasks."""

import argparse
import logging
import sys
from pathlib import Path

from channels_operator.pipeline.orchestrator import VALID_STAGES, run_pipeline
from channels_operator.storage.db import init_db
from channels_operator.storage.repository import cost_summary, get_drafts_with_items


CHANNELS_DIR = Path("channels")


def _write_review_markdown(channel_id: str, path: Path) -> None:
    """Write all pending drafts for the channel to a markdown review file."""
    pairs = get_drafts_with_items(channel_id, state="pending")
    lines: list[str] = [
        f"# Review — channel: {channel_id}",
        "",
        f"Pending drafts: {len(pairs)}",
        "",
        "Mark each as ACCEPT / EDIT / REJECT. Where you EDIT, paste the",
        "edited Hebrew so we can use the diff to tune the prompt.",
        "",
        "---",
        "",
    ]
    for idx, (draft, item) in enumerate(pairs, 1):
        lines.extend(
            [
                f"## Item {idx}: {item.title}",
                f"**Source:** `{item.source}`  ·  **URL:** {item.url}",
                f"**Stage 2:** score={item.stage2_score}  ·  axis={item.stage2_axis}  ·  topic={item.stage2_topic_tag}",
                "",
                "### Hebrew draft",
                "",
                f"**Headline:** {draft.headline_finding}",
                "",
                "**Body:**",
                "",
                draft.post_body or "_(empty)_",
                "",
                f"**Hashtags:** {draft.hashtags}",
                "",
                f"**Caveat:** {draft.methodological_caveat or '_(none)_'}",
                "",
                "---",
                "",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def cmd_pipeline_run(args: argparse.Namespace) -> int:
    init_db()
    stages = args.stages.split(",") if args.stages else list(VALID_STAGES)
    result = run_pipeline(
        args.channel,
        CHANNELS_DIR,
        limit=args.limit,
        stages=stages,
    )

    print(f"\nPipeline run for channel: {args.channel}")
    if "stage1" in stages:
        print(f"\n{'Source':<42} {'Fetched':>9} {'Stage 1':>9} {'Stored':>9}")
        print("-" * 72)
        total_fetched = total_passed = total_stored = 0
        for name, s in result["stage1"]["sources"].items():
            if s.get("error"):
                print(f"{name:<42} {'FAILED':>9}")
                print(f"  -> {s['error']}")
                continue
            print(
                f"{name:<42} {s['fetched']:>9} {s['stage1_pass']:>9} {s['stored_new']:>9}"
            )
            total_fetched += s["fetched"]
            total_passed += s["stage1_pass"]
            total_stored += s["stored_new"]
        print("-" * 72)
        print(f"{'TOTAL':<42} {total_fetched:>9} {total_passed:>9} {total_stored:>9}")

    if "stage2" in stages:
        s2 = result["stage2"]
        print(
            f"\nStage 2: scored={s2['scored']}  passed={s2['passed']}  errors={s2['errors']}"
        )

    if "stage3" in stages:
        s3 = result["stage3"]
        print(f"Stage 3: summarized={s3['summarized']}  errors={s3['errors']}")

    # Cost summary for this channel
    summary = cost_summary(args.channel)
    if summary:
        print("\nCost log (cumulative per stage / model):")
        for stage, by_model in summary.items():
            for model, m in by_model.items():
                print(
                    f"  {stage:<7} {model:<35} calls={m['calls']:<5} "
                    f"in={m['prompt_tokens']:<7} out={m['completion_tokens']}"
                )

    if args.review_out:
        out_path = Path(args.review_out)
        _write_review_markdown(args.channel, out_path)
        print(f"\nReview file: {out_path}")

    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(prog="channels-operator")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pipeline = sub.add_parser("pipeline", help="Pipeline operations")
    pipeline_sub = pipeline.add_subparsers(dest="action", required=True)

    run = pipeline_sub.add_parser("run", help="Run the pipeline once for a channel")
    run.add_argument("--channel", required=True, help="Channel id (folder under channels/)")
    run.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap on items processed by Stages 2/3 this run",
    )
    run.add_argument(
        "--stages",
        default=None,
        help=f"Comma-separated subset of {','.join(VALID_STAGES)} (default: all)",
    )
    run.add_argument(
        "--review-out",
        default=None,
        help="Write a markdown review of produced drafts to this path",
    )
    run.set_defaults(func=cmd_pipeline_run)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
