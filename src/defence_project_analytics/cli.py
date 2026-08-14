"""Command-line interface for read-only foundation checks and Stage Overview."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from defence_project_analytics.bigquery_client import get_client
from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.contract_validation import validate_contracts
from defence_project_analytics.errors import AnalyticsFoundationError
from defence_project_analytics.smoke import run_connection_smoke
from defence_project_analytics.stage_difficulty import (
    DEFAULT_MAXIMUM_TOTAL_BYTES,
    StageDifficultyRequest,
    generate_stage_difficulty_report,
)
from defence_project_analytics.reporting.warnings import WarningThresholds
from defence_project_analytics.stage_overview import (
    StageOverviewRequest,
    generate_stage_overview_report,
    get_stage_overview,
)


def _json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False))


def _parse_bool(value: str) -> bool:
    normalized = value.casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a UTC offset or Z")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    defaults = AnalyticsConfig.from_env()
    parser = argparse.ArgumentParser(
        prog="defence-analytics",
        description="Read-only DefenceProject BigQuery analytics foundation",
    )
    parser.add_argument("--project", default=defaults.project_id)
    parser.add_argument("--dataset", default=defaults.dataset_id)
    parser.add_argument("--location", default=defaults.location)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("connection-smoke", help="Check six required objects with read-only queries")
    subparsers.add_parser(
        "validate-contracts", help="Parse local contracts and validate required live columns"
    )

    overview = subparsers.add_parser("stage-overview", help="Calculate aggregate stage metrics")
    overview.add_argument("--stage-key", required=True)
    overview.add_argument("--environment", required=True, choices=("Production", "Test"))
    overview.add_argument("--content-version", type=int)
    overview.add_argument(
        "--maximum-bytes-billed",
        type=int,
        help="Optional BigQuery safety cap for this aggregate query",
    )
    overview.add_argument("--output-root", type=Path)
    overview.add_argument("--overwrite", action="store_true")

    difficulty = subparsers.add_parser("stage-difficulty", help="Generate aggregate Stage Difficulty report bundle")
    difficulty.add_argument("--environment", required=True, choices=("Production", "Test"))
    difficulty.add_argument("--stage-key", required=True)
    difficulty.add_argument("--content-version", required=True, type=int)
    difficulty.add_argument("--app-version")
    difficulty.add_argument("--release-id")
    difficulty.add_argument("--release-channel")
    difficulty.add_argument("--release-type")
    difficulty.add_argument("--development-build", type=_parse_bool)
    difficulty.add_argument("--uploaded-start-utc", type=_parse_datetime)
    difficulty.add_argument("--uploaded-end-utc", type=_parse_datetime)
    difficulty.add_argument("--output-root", type=Path, default=Path("reports/generated"))
    difficulty.add_argument("--overwrite", action="store_true")
    difficulty.add_argument("--maximum-total-bytes", type=int, default=DEFAULT_MAXIMUM_TOTAL_BYTES)
    difficulty.add_argument("--min-final-attempts", type=int, default=30)
    difficulty.add_argument("--min-unique-players", type=int, default=10)
    difficulty.add_argument("--min-deaths", type=int, default=20)
    difficulty.add_argument("--min-detail-runs", type=int, default=20)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = AnalyticsConfig(args.project, args.dataset, args.location)
    try:
        client = get_client(config)
        if args.command == "connection-smoke":
            results = run_connection_smoke(client=client, config=config)
            payload = {
                "ready": all(item.exists and item.query_accessible for item in results),
                "results": [item.to_dict() for item in results],
            }
            _json(payload)
            return 0 if payload["ready"] else 1

        if args.command == "validate-contracts":
            report = validate_contracts(client=client, config=config)
            _json(report.to_dict())
            return 0 if report.ready else 1

        if args.command == "stage-overview":
            request = StageOverviewRequest(
                stage_key=args.stage_key,
                environment=args.environment,
                content_version=args.content_version,
            )
            if args.output_root is not None and request.content_version is None:
                raise ValueError("--content-version is required with --output-root")
            result = get_stage_overview(
                request,
                client=client,
                config=config,
                maximum_bytes_billed=args.maximum_bytes_billed,
            )
            if args.output_root is not None:
                generate_stage_overview_report(
                    request,
                    result,
                    output_root=args.output_root,
                    overwrite=args.overwrite,
                )
            _json(
                {
                    "filters": {
                        "stage_key": request.stage_key,
                        "environment": request.environment,
                        "content_version": request.content_version,
                    },
                    "metrics": result,
                }
            )
            return 0

        if args.command == "stage-difficulty":
            request = StageDifficultyRequest(
                environment=args.environment,
                stage_key=args.stage_key,
                content_version=args.content_version,
                app_version=args.app_version,
                release_id=args.release_id,
                release_channel=args.release_channel,
                release_type=args.release_type,
                is_development_build=args.development_build,
                uploaded_at_utc_start=args.uploaded_start_utc,
                uploaded_at_utc_end=args.uploaded_end_utc,
            )
            path = generate_stage_difficulty_report(
                request,
                output_root=args.output_root,
                overwrite=args.overwrite,
                client=client,
                config=config,
                maximum_total_bytes=args.maximum_total_bytes,
                thresholds=WarningThresholds(
                    args.min_final_attempts,
                    args.min_unique_players,
                    args.min_deaths,
                    args.min_detail_runs,
                ),
            )
            metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
            _json(
                {
                    "reportPath": str(path.resolve()),
                    "scope": metadata["scope"],
                    "sample": metadata["sample"],
                    "warningCodes": [warning["code"] for warning in metadata["warnings"]],
                    "dryRunEstimatedBytes": metadata["dryRunEstimatedBytes"],
                }
            )
            return 0
    except AnalyticsFoundationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Analytics command failed: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
