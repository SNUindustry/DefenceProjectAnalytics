"""Command-line interface for read-only foundation checks and Stage Overview."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from defence_project_analytics.bigquery_client import get_client
from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.contract_validation import validate_contracts
from defence_project_analytics.errors import AnalyticsFoundationError
from defence_project_analytics.smoke import run_connection_smoke
from defence_project_analytics.stage_overview import StageOverviewRequest, get_stage_overview


def _json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


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
            result = get_stage_overview(
                request,
                client=client,
                config=config,
                maximum_bytes_billed=args.maximum_bytes_billed,
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
    except AnalyticsFoundationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Analytics command failed: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

