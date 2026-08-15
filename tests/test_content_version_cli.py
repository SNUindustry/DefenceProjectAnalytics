from defence_project_analytics.cli import build_parser


def test_comparison_cli_accepts_direction_domains_and_snapshot() -> None:
    args = build_parser().parse_args([
        "content-version-compare", "--environment", "Test",
        "--baseline-content-version", "1", "--candidate-content-version", "4",
        "--stage-key", "stage1", "--domains", "stage,weapon,post-run",
        "--as-of-utc", "2026-08-15T00:00:00Z", "--post-run-max-gap-minutes", "45",
    ])
    assert args.baseline_content_version == 1
    assert args.candidate_content_version == 4
    assert args.domains == ("stage", "weapon", "post-run")
    assert args.as_of_utc.tzinfo is not None
    assert args.post_run_max_gap_minutes == 45
