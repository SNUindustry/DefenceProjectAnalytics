from defence_project_analytics.cli import build_parser


def test_cli_accepts_snapshot_and_explicit_threshold_pair() -> None:
    args = build_parser().parse_args([
        "run-retention", "--environment", "Test", "--content-version", "1",
        "--stage-key", "stage1", "--as-of-utc", "2026-08-15T00:00:00Z",
        "--long-term-no-next-run-threshold-days", "7",
        "--source-upload-grace-hours", "24",
    ])
    assert args.command == "run-retention"
    assert args.long_term_no_next_run_threshold_days == 7
    assert args.source_upload_grace_hours == 24
    assert args.as_of_utc.tzinfo is not None
