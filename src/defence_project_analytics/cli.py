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
from defence_project_analytics.weapon_performance import (
    DEFAULT_MAXIMUM_TOTAL_BYTES as WEAPON_DEFAULT_MAXIMUM_TOTAL_BYTES,
    WeaponPerformanceRequest,
    generate_weapon_performance_report,
)
from defence_project_analytics.upgrade_choice import (
    DEFAULT_MAXIMUM_TOTAL_BYTES as UPGRADE_DEFAULT_MAXIMUM_TOTAL_BYTES,
    UpgradeChoiceRequest,
    generate_upgrade_choice_report,
)
from defence_project_analytics.progression_next_run import (
    DEFAULT_MAXIMUM_TOTAL_BYTES as PROGRESSION_DEFAULT_MAXIMUM_TOTAL_BYTES,
    ProgressionNextRunRequest,
    generate_progression_next_run_report,
)
from defence_project_analytics.post_run_behavior import (
    DEFAULT_MAXIMUM_TOTAL_BYTES as POST_RUN_DEFAULT_MAXIMUM_TOTAL_BYTES,
    PostRunBehaviorRequest,
    generate_post_run_behavior_report,
)
from defence_project_analytics.content_version_comparison import (
    DEFAULT_MAXIMUM_TOTAL_BYTES as COMPARISON_DEFAULT_MAXIMUM_TOTAL_BYTES,
    ContentVersionCompareRequest,
    generate_content_version_comparison_report,
)
from defence_project_analytics.analysis_brief import (
    AnalysisBriefRequest,
    generate_analysis_brief,
)
from defence_project_analytics.brief.models import EvidenceSelectionPolicy
from defence_project_analytics.llm_analysis import (
    AnalysisPromptRequest,
    AnalysisResponseValidationError,
    AnthropicAnalysisProvider,
    DEFAULT_ANTHROPIC_MAX_OUTPUT_TOKENS,
    DEFAULT_ANTHROPIC_MODEL,
    generate_analysis_prompt,
    generate_validated_analysis,
    run_analysis_with_provider,
)
from defence_project_analytics.llm_analysis.provider_errors import AnalysisProviderError


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


def _parse_domains(value: str) -> tuple[str, ...]:
    domains = tuple(item.strip() for item in value.split(",") if item.strip())
    if not domains:
        raise argparse.ArgumentTypeError("expected one or more comma-separated domains")
    return domains


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

    weapon = subparsers.add_parser("weapon-performance", help="Generate aggregate Weapon Performance report bundle")
    weapon.add_argument("--environment", required=True, choices=("Production", "Test"))
    weapon.add_argument("--stage-key", required=True)
    weapon.add_argument("--content-version", required=True, type=int)
    weapon.add_argument("--app-version")
    weapon.add_argument("--release-id")
    weapon.add_argument("--release-channel")
    weapon.add_argument("--release-type")
    weapon.add_argument("--development-build", type=_parse_bool)
    weapon.add_argument("--segment-ended-start-utc", type=_parse_datetime)
    weapon.add_argument("--segment-ended-end-utc", type=_parse_datetime)
    weapon.add_argument("--uploaded-start-utc", type=_parse_datetime)
    weapon.add_argument("--uploaded-end-utc", type=_parse_datetime)
    weapon.add_argument("--as-of-utc", type=_parse_datetime)
    weapon.add_argument("--output-root", type=Path, default=Path("reports/generated"))
    weapon.add_argument("--overwrite", action="store_true")
    weapon.add_argument("--maximum-total-bytes", type=int, default=WEAPON_DEFAULT_MAXIMUM_TOTAL_BYTES)

    upgrade = subparsers.add_parser("upgrade-choice", help="Generate aggregate Upgrade Choice report bundle")
    upgrade.add_argument("--environment", required=True, choices=("Production", "Test"))
    upgrade.add_argument("--stage-key", required=True)
    upgrade.add_argument("--content-version", required=True, type=int)
    upgrade.add_argument("--app-version")
    upgrade.add_argument("--release-id")
    upgrade.add_argument("--release-channel")
    upgrade.add_argument("--release-type")
    upgrade.add_argument("--development-build", type=_parse_bool)
    upgrade.add_argument("--segment-ended-start-utc", type=_parse_datetime)
    upgrade.add_argument("--segment-ended-end-utc", type=_parse_datetime)
    upgrade.add_argument("--uploaded-start-utc", type=_parse_datetime)
    upgrade.add_argument("--uploaded-end-utc", type=_parse_datetime)
    upgrade.add_argument("--as-of-utc", type=_parse_datetime)
    upgrade.add_argument("--output-root", type=Path, default=Path("reports/generated"))
    upgrade.add_argument("--overwrite", action="store_true")
    upgrade.add_argument("--maximum-total-bytes", type=int, default=UPGRADE_DEFAULT_MAXIMUM_TOTAL_BYTES)

    progression = subparsers.add_parser(
        "progression-next-run", help="Generate aggregate Progression Next-Run report bundle"
    )
    progression.add_argument("--environment", required=True, choices=("Production", "Test"))
    progression.add_argument("--content-version", required=True, type=int)
    progression.add_argument("--progression-kind")
    progression.add_argument("--previous-stage-key")
    progression.add_argument("--next-stage-key")
    progression.add_argument("--app-version")
    progression.add_argument("--release-id")
    progression.add_argument("--release-channel")
    progression.add_argument("--release-type")
    progression.add_argument("--development-build", type=_parse_bool)
    progression.add_argument("--progression-start-utc", type=_parse_datetime)
    progression.add_argument("--progression-end-utc", type=_parse_datetime)
    progression.add_argument("--uploaded-start-utc", type=_parse_datetime)
    progression.add_argument("--uploaded-end-utc", type=_parse_datetime)
    progression.add_argument("--as-of-utc", type=_parse_datetime)
    progression.add_argument("--previous-run-max-gap-minutes", type=int, default=30)
    progression.add_argument("--next-run-max-gap-minutes", type=int, default=30)
    progression.add_argument("--output-root", type=Path, default=Path("reports/generated"))
    progression.add_argument("--overwrite", action="store_true")
    progression.add_argument(
        "--maximum-total-bytes", type=int, default=PROGRESSION_DEFAULT_MAXIMUM_TOTAL_BYTES
    )
    post_run = subparsers.add_parser(
        "post-run-behavior", help="Generate aggregate Post-Run Behavior report bundle"
    )
    post_run.add_argument("--environment", required=True, choices=("Production", "Test"))
    post_run.add_argument("--content-version", required=True, type=int)
    post_run.add_argument("--stage-key")
    post_run.add_argument("--final-outcome", choices=("Clear", "Dead", "Abandon"))
    post_run.add_argument("--app-version")
    post_run.add_argument("--release-id")
    post_run.add_argument("--release-channel")
    post_run.add_argument("--release-type")
    post_run.add_argument("--development-build", type=_parse_bool)
    post_run.add_argument("--run-ended-start-utc", type=_parse_datetime)
    post_run.add_argument("--run-ended-end-utc", type=_parse_datetime)
    post_run.add_argument("--uploaded-start-utc", type=_parse_datetime)
    post_run.add_argument("--uploaded-end-utc", type=_parse_datetime)
    post_run.add_argument("--as-of-utc", type=_parse_datetime)
    post_run.add_argument("--post-run-max-gap-minutes", type=int, default=30)
    post_run.add_argument("--output-root", type=Path, default=Path("reports/generated"))
    post_run.add_argument("--overwrite", action="store_true")
    post_run.add_argument(
        "--maximum-total-bytes", type=int, default=POST_RUN_DEFAULT_MAXIMUM_TOTAL_BYTES
    )
    comparison = subparsers.add_parser(
        "content-version-compare", help="Compare aggregate analytics across two contentVersions"
    )
    comparison.add_argument("--environment", required=True, choices=("Production", "Test"))
    comparison.add_argument("--baseline-content-version", required=True, type=int)
    comparison.add_argument("--candidate-content-version", required=True, type=int)
    comparison.add_argument("--stage-key")
    comparison.add_argument("--domains", type=_parse_domains)
    comparison.add_argument("--app-version")
    comparison.add_argument("--release-channel")
    comparison.add_argument("--release-type")
    comparison.add_argument("--development-build", type=_parse_bool)
    comparison.add_argument("--uploaded-start-utc", type=_parse_datetime)
    comparison.add_argument("--uploaded-end-utc", type=_parse_datetime)
    comparison.add_argument("--as-of-utc", type=_parse_datetime)
    comparison.add_argument("--previous-run-max-gap-minutes", type=int, default=30)
    comparison.add_argument("--next-run-max-gap-minutes", type=int, default=30)
    comparison.add_argument("--post-run-max-gap-minutes", type=int, default=30)
    comparison.add_argument("--output-root", type=Path, default=Path("reports/generated"))
    comparison.add_argument("--overwrite", action="store_true")
    comparison.add_argument(
        "--maximum-total-bytes", type=int, default=COMPARISON_DEFAULT_MAXIMUM_TOTAL_BYTES
    )
    brief = subparsers.add_parser(
        "analysis-brief", help="Compile local aggregate reports into an evidence brief"
    )
    brief.add_argument(
        "--mode",
        required=True,
        choices=("single-version", "content-version-compare"),
    )
    brief.add_argument(
        "--source-report", type=Path, action="append", required=True,
        help="Explicit generated report bundle path; may be repeated in single mode",
    )
    brief.add_argument("--output-root", type=Path, default=Path("reports/generated"))
    brief.add_argument("--overwrite", action="store_true")
    brief.add_argument("--max-brief-characters", type=int, default=48_000)
    brief.add_argument("--max-evidence-items", type=int, default=120)
    brief.add_argument("--max-evidence-items-per-domain", type=int, default=30)
    brief.add_argument("--core-evidence-minimum-per-domain", type=int, default=3)
    analysis_prompt = subparsers.add_parser(
        "analysis-prompt", help="Create a local provider-neutral C-2 prompt package"
    )
    analysis_prompt.add_argument("--source-brief", type=Path, required=True)
    analysis_prompt.add_argument("--analysis-objective")
    analysis_prompt.add_argument("--output-language", choices=("ko", "en"), default="ko")
    analysis_prompt.add_argument("--max-prompt-characters", type=int, default=400_000)
    analysis_prompt.add_argument("--output-root", type=Path, default=Path("reports/generated"))
    analysis_prompt.add_argument("--overwrite", action="store_true")
    analysis_validate = subparsers.add_parser(
        "analysis-validate", help="Validate an external C-2 JSON response and render a report"
    )
    analysis_validate.add_argument("--analysis-request", type=Path, required=True)
    analysis_validate.add_argument("--response", type=Path, required=True)
    analysis_validate.add_argument(
        "--source-brief", type=Path,
        help="Required only when the request has no workspace-relative source path",
    )
    analysis_validate.add_argument("--provider-name", default="external")
    analysis_validate.add_argument("--model-name")
    analysis_validate.add_argument("--output-root", type=Path, default=Path("reports/generated"))
    analysis_validate.add_argument("--overwrite", action="store_true")
    analysis_run = subparsers.add_parser(
        "analysis-run", help="Run C-2 through the native Anthropic strict-tools API"
    )
    analysis_run.add_argument("--source-brief", type=Path, required=True)
    analysis_run.add_argument("--provider", choices=("anthropic",), default="anthropic")
    analysis_run.add_argument("--model", default=DEFAULT_ANTHROPIC_MODEL)
    analysis_run.add_argument("--analysis-objective")
    analysis_run.add_argument("--output-language", choices=("ko", "en"), default="ko")
    analysis_run.add_argument("--max-prompt-characters", type=int, default=400_000)
    analysis_run.add_argument(
        "--max-output-tokens", type=int, default=DEFAULT_ANTHROPIC_MAX_OUTPUT_TOKENS
    )
    analysis_run.add_argument("--output-root", type=Path, default=Path("reports/generated"))
    analysis_run.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "analysis-run":
        try:
            provider = AnthropicAnalysisProvider(
                model_name=args.model,
                max_output_tokens=args.max_output_tokens,
            )
            path = run_analysis_with_provider(
                AnalysisPromptRequest(
                    source_brief_path=args.source_brief,
                    analysis_objective=args.analysis_objective,
                    output_language=args.output_language,
                    max_prompt_characters=args.max_prompt_characters,
                ),
                provider,
                output_root=args.output_root,
                overwrite=args.overwrite,
            )
            analysis = json.loads((path / "analysis.json").read_text(encoding="utf-8"))
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            _json({
                "reportPath": str(path.resolve()),
                "analysisExecutionId": manifest["analysisExecutionId"],
                "providerName": manifest["providerName"],
                "modelName": manifest["modelName"],
                "inputTokenCount": manifest.get("inputTokenCount"),
                "actualInputTokens": manifest.get("actualInputTokens"),
                "actualOutputTokens": manifest.get("actualOutputTokens"),
                "providerCallCount": manifest["providerCallCount"],
                "providerRetryCount": manifest.get("providerRetryCount"),
                "validatorStatus": manifest["validationStatus"],
                "overallAssessment": analysis["overallAssessment"],
                "estimatedBytes": 0,
            })
            return 0
        except AnalysisResponseValidationError as exc:
            metadata = getattr(provider, "last_run_metadata", {})
            stages = metadata.get("stages", []) if isinstance(metadata, dict) else []
            failed_stage = next(
                (
                    stage for stage in reversed(stages)
                    if isinstance(stage, dict)
                    and stage.get("validationStatus") == "failed"
                ),
                None,
            )
            stage_diagnostics = None
            if failed_stage is not None:
                stage_diagnostics = {
                    key: failed_stage.get(key)
                    for key in (
                        "stage", "contextDigest", "schemaDigest", "requestDigest",
                        "preflightInputTokens", "actualInputTokens", "actualOutputTokens",
                        "tokenCountCallCount", "generationCallCount", "retryCount",
                        "validationStatus", "safeRequestId", "stopReason",
                        "expectedToolCount", "observedToolCount", "ignoredTextBlockCount",
                    )
                }
            print(json.dumps({
                "errorCode": "ANALYSIS_RESPONSE_VALIDATION_FAILED",
                "validationStatus": "Invalid",
                "issues": exc.issues,
                "modelName": getattr(provider, "model_name", None),
                "inputTokenCount": metadata.get("inputTokenCount"),
                "actualInputTokens": metadata.get("actualInputTokens"),
                "actualOutputTokens": metadata.get("actualOutputTokens"),
                "providerCallCount": metadata.get("providerCallCount"),
                "providerTokenCountCallCount": metadata.get(
                    "providerTokenCountCallCount"
                ),
                "providerGenerationCallCount": metadata.get(
                    "providerGenerationCallCount"
                ),
                "providerRetryCount": metadata.get("providerRetryCount"),
                "stageDiagnostics": stage_diagnostics,
            }, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        except AnalysisProviderError as exc:
            metadata = getattr(provider, "last_run_metadata", {})
            print(json.dumps({
                "errorCode": exc.code,
                "message": str(exc),
                "modelName": getattr(provider, "model_name", None),
                "inputTokenCount": metadata.get("inputTokenCount"),
                "actualInputTokens": metadata.get("actualInputTokens"),
                "actualOutputTokens": metadata.get("actualOutputTokens"),
                "providerCallCount": metadata.get("providerCallCount"),
                "providerTokenCountCallCount": metadata.get(
                    "providerTokenCountCallCount"
                ),
                "providerGenerationCallCount": metadata.get(
                    "providerGenerationCallCount"
                ),
                "providerErrorCount": metadata.get("providerErrorCount"),
                "providerRetryCount": metadata.get("providerRetryCount"),
                "providerDiagnostics": getattr(exc, "details", {}),
            }, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        except Exception as exc:
            print(json.dumps({
                "errorCode": "ANALYSIS_RUN_FAILED",
                "errorType": type(exc).__name__,
            }, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
    # C-2 local commands run before any ADC or BigQuery client creation.
    if args.command == "analysis-prompt":
        try:
            path = generate_analysis_prompt(
                AnalysisPromptRequest(
                    source_brief_path=args.source_brief,
                    analysis_objective=args.analysis_objective,
                    output_language=args.output_language,
                    max_prompt_characters=args.max_prompt_characters,
                ),
                output_root=args.output_root,
                overwrite=args.overwrite,
            )
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            _json({
                "requestPath": str(path.resolve()),
                "requestId": manifest["requestId"],
                "sourceBriefIdentity": manifest["sourceBriefIdentity"],
                "promptDigest": manifest["promptDigest"],
                "promptCharacterCount": manifest["promptCharacterCount"],
                "evidenceCount": manifest["evidenceCount"],
                "providerCalls": 0,
                "estimatedBytes": 0,
            })
            return 0
        except Exception as exc:
            print(f"Analysis prompt command failed: {exc}", file=sys.stderr)
            return 1
    if args.command == "analysis-validate":
        try:
            path = generate_validated_analysis(
                analysis_request_path=args.analysis_request,
                response=args.response,
                source_brief_path=args.source_brief,
                output_root=args.output_root,
                provider_name=args.provider_name,
                model_name=args.model_name,
                overwrite=args.overwrite,
            )
            analysis = json.loads((path / "analysis.json").read_text(encoding="utf-8"))
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            _json({
                "reportPath": str(path.resolve()),
                "analysisExecutionId": manifest["analysisExecutionId"],
                "overallAssessment": analysis["overallAssessment"],
                "observationCount": len(analysis["observations"]),
                "hypothesisCount": len(analysis["hypotheses"]),
                "changeCandidateCount": len(analysis["changeCandidates"]),
                "validationStatus": manifest["validationStatus"],
                "providerCalls": manifest["providerCallCount"],
                "estimatedBytes": 0,
            })
            return 0
        except AnalysisResponseValidationError as exc:
            print(json.dumps({"validationStatus": "Invalid", "issues": exc.issues}, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"Analysis validation command failed: {exc}", file=sys.stderr)
            return 1
    # C-1 is deliberately handled before client creation: this command must not
    # load ADC, contact BigQuery, or execute a source analyzer.
    if args.command == "analysis-brief":
        try:
            request = AnalysisBriefRequest(
                mode=args.mode,
                source_report_paths=tuple(args.source_report),
                selection_policy=EvidenceSelectionPolicy(
                    max_brief_characters=args.max_brief_characters,
                    max_evidence_items=args.max_evidence_items,
                    max_evidence_items_per_domain=args.max_evidence_items_per_domain,
                    core_evidence_minimum_per_domain=args.core_evidence_minimum_per_domain,
                ),
            )
            path = generate_analysis_brief(
                request,
                output_root=args.output_root,
                overwrite=args.overwrite,
            )
            brief_payload = json.loads((path / "brief.json").read_text(encoding="utf-8"))
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            summary = brief_payload["selectionSummary"]
            scope = brief_payload["scope"]
            included = list(scope["domains"])
            all_domains = {
                "stageDifficulty", "weaponPerformance", "upgradeChoice",
                "progressionNextRun", "postRunBehavior",
            }
            _json({
                "reportPath": str(path.resolve()),
                "mode": brief_payload["mode"],
                "scope": scope,
                "overallStatus": brief_payload["overallStatus"],
                "domainsIncluded": included,
                "domainsMissing": sorted(all_domains.difference(included)),
                "warningCodes": [
                    item["code"] for item in brief_payload["criticalWarnings"]
                ],
                "selectedEvidenceCount": summary["selectedEvidenceCount"],
                "omittedEvidenceCount": summary["omittedEvidenceCount"],
                "coreSelectedByDomain": summary["coreSelectedByDomain"],
                "truncated": summary["truncated"],
                "briefCharacterCount": summary["briefCharacterCount"],
                "estimatedBytes": manifest["bigQueryEstimatedBytes"],
            })
            return 0
        except Exception as exc:
            print(f"Analysis Brief command failed: {exc}", file=sys.stderr)
            return 1
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

        if args.command == "weapon-performance":
            request = WeaponPerformanceRequest(
                environment=args.environment,
                stage_key=args.stage_key,
                content_version=args.content_version,
                app_version=args.app_version,
                release_id=args.release_id,
                release_channel=args.release_channel,
                release_type=args.release_type,
                is_development_build=args.development_build,
                segment_ended_at_utc_start=args.segment_ended_start_utc,
                segment_ended_at_utc_end=args.segment_ended_end_utc,
                uploaded_at_utc_start=args.uploaded_start_utc,
                uploaded_at_utc_end=args.uploaded_end_utc,
                analysis_as_of_utc=args.as_of_utc,
            )
            path = generate_weapon_performance_report(
                request,
                output_root=args.output_root,
                overwrite=args.overwrite,
                client=client,
                config=config,
                maximum_total_bytes=args.maximum_total_bytes,
            )
            metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
            _json({
                "reportPath": str(path.resolve()),
                "scope": metadata["scope"],
                "weaponFamilyCount": metadata["sample"]["weaponFamilyCount"],
                "detailEligibleAttempts": metadata["sample"]["detailEligibleAttempts"],
                "warningCodes": [warning["code"] for warning in metadata["warnings"]],
                "estimatedBytes": metadata["dryRunEstimatedBytes"],
            })
            return 0

        if args.command == "upgrade-choice":
            request = UpgradeChoiceRequest(
                environment=args.environment,
                stage_key=args.stage_key,
                content_version=args.content_version,
                app_version=args.app_version,
                release_id=args.release_id,
                release_channel=args.release_channel,
                release_type=args.release_type,
                is_development_build=args.development_build,
                segment_ended_at_utc_start=args.segment_ended_start_utc,
                segment_ended_at_utc_end=args.segment_ended_end_utc,
                uploaded_at_utc_start=args.uploaded_start_utc,
                uploaded_at_utc_end=args.uploaded_end_utc,
                analysis_as_of_utc=args.as_of_utc,
            )
            path = generate_upgrade_choice_report(
                request,
                output_root=args.output_root,
                overwrite=args.overwrite,
                client=client,
                config=config,
                maximum_total_bytes=args.maximum_total_bytes,
            )
            metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
            _json({
                "reportPath": str(path.resolve()),
                "scope": metadata["scope"],
                "candidateCount": metadata["sample"]["candidateCount"],
                "completeExposureCount": metadata["sample"]["completeExposures"],
                "fullyChoiceCoveredAttempts": metadata["sample"]["fullyChoiceCoveredAttempts"],
                "warningCodes": [warning["code"] for warning in metadata["warnings"]],
                "estimatedBytes": metadata["dryRunEstimatedBytes"],
            })
            return 0

        if args.command == "progression-next-run":
            request = ProgressionNextRunRequest(
                environment=args.environment,
                content_version=args.content_version,
                progression_kind=args.progression_kind,
                previous_stage_key=args.previous_stage_key,
                next_stage_key=args.next_stage_key,
                app_version=args.app_version,
                release_id=args.release_id,
                release_channel=args.release_channel,
                release_type=args.release_type,
                is_development_build=args.development_build,
                progression_occurred_at_utc_start=args.progression_start_utc,
                progression_occurred_at_utc_end=args.progression_end_utc,
                uploaded_at_utc_start=args.uploaded_start_utc,
                uploaded_at_utc_end=args.uploaded_end_utc,
                analysis_as_of_utc=args.as_of_utc,
                previous_run_max_gap_minutes=args.previous_run_max_gap_minutes,
                next_run_max_gap_minutes=args.next_run_max_gap_minutes,
            )
            path = generate_progression_next_run_report(
                request,
                output_root=args.output_root,
                overwrite=args.overwrite,
                client=client,
                config=config,
                maximum_total_bytes=args.maximum_total_bytes,
            )
            metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
            sample = metadata["sample"]
            _json({
                "reportPath": str(path.resolve()),
                "scope": metadata["scope"],
                "progressionEventCount": sample["progressionEvents"],
                "boundedEpisodeCount": sample["boundedEpisodes"],
                "unboundedProgressionEventCount": sample["unboundedProgressionEvents"],
                "matureEpisodeCount": sample["matureEpisodes"],
                "nextRunLinkedEpisodeCount": sample["episodesWithNextRun"],
                "sameStagePairedEpisodeCount": sample["sameStagePairedEpisodes"],
                "warningCodes": [warning["code"] for warning in metadata["warnings"]],
                "estimatedBytes": metadata["dryRunEstimatedBytes"],
            })
            return 0

        if args.command == "post-run-behavior":
            request = PostRunBehaviorRequest(
                environment=args.environment,
                content_version=args.content_version,
                stage_key=args.stage_key,
                final_outcome=args.final_outcome,
                app_version=args.app_version,
                release_id=args.release_id,
                release_channel=args.release_channel,
                release_type=args.release_type,
                is_development_build=args.development_build,
                run_ended_at_utc_start=args.run_ended_start_utc,
                run_ended_at_utc_end=args.run_ended_end_utc,
                uploaded_at_utc_start=args.uploaded_start_utc,
                uploaded_at_utc_end=args.uploaded_end_utc,
                analysis_as_of_utc=args.as_of_utc,
                post_run_max_gap_minutes=args.post_run_max_gap_minutes,
            )
            path = generate_post_run_behavior_report(
                request,
                output_root=args.output_root,
                overwrite=args.overwrite,
                client=client,
                config=config,
                maximum_total_bytes=args.maximum_total_bytes,
            )
            metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
            sample = metadata["sample"]
            _json({
                "reportPath": str(path.resolve()),
                "scope": metadata["scope"],
                "anchorFinalRunCount": sample["anchorFinalRuns"],
                "matureWindowCount": sample["matureWindows"],
                "rightCensoredWindowCount": sample["rightCensoredWindows"],
                "shopPresentedWindowCount": sample["shopPresentedWindows"],
                "shopUserNavigatedWindowCount": sample["shopUserNavigatedWindows"],
                "progressionWindowCount": sample["progressionWindows"],
                "commerceAttemptWindowCount": sample["commerceAttemptWindows"],
                "committedSuccessWindowCount": sample["committedSuccessWindows"],
                "nextRunWithinWindowCount": sample["nextRunWithinWindowWindows"],
                "warningCodes": [warning["code"] for warning in metadata["warnings"]],
                "estimatedBytes": metadata["dryRunEstimatedBytes"],
            })
            return 0

        if args.command == "content-version-compare":
            request = ContentVersionCompareRequest(
                environment=args.environment,
                baseline_content_version=args.baseline_content_version,
                candidate_content_version=args.candidate_content_version,
                stage_key=args.stage_key,
                domains=args.domains,
                app_version=args.app_version,
                release_channel=args.release_channel,
                release_type=args.release_type,
                is_development_build=args.development_build,
                uploaded_at_utc_start=args.uploaded_start_utc,
                uploaded_at_utc_end=args.uploaded_end_utc,
                analysis_as_of_utc=args.as_of_utc,
                previous_run_max_gap_minutes=args.previous_run_max_gap_minutes,
                next_run_max_gap_minutes=args.next_run_max_gap_minutes,
                post_run_max_gap_minutes=args.post_run_max_gap_minutes,
            )
            path = generate_content_version_comparison_report(
                request,
                output_root=args.output_root,
                overwrite=args.overwrite,
                client=client,
                config=config,
                maximum_total_bytes=args.maximum_total_bytes,
            )
            metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
            quality = metadata["quality"]
            source_domains = {
                item["domain"] for item in metadata.get("sourceAnalyses", [])
            }
            warning_codes = [item["code"] for item in metadata["warnings"]]
            _json({
                "reportPath": str(path.resolve()),
                "environment": request.environment,
                "baselineContentVersion": request.baseline_content_version,
                "candidateContentVersion": request.candidate_content_version,
                "domainsCompared": sorted(source_domains),
                "domainsLimited": quality["domainsLimited"],
                "domainsUnavailable": quality["domainsUnavailable"],
                "warningCodes": warning_codes,
                "estimatedBytes": metadata["dryRunEstimatedBytes"],
            })
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
