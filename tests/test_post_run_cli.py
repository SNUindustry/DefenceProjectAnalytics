from defence_project_analytics.cli import build_parser


def test_post_run_cli_accepts_scope_window_and_snapshot() -> None:
    args = build_parser().parse_args([
        "post-run-behavior", "--environment", "Test", "--content-version", "4",
        "--stage-key", "stage1", "--final-outcome", "Dead",
        "--development-build", "false", "--run-ended-start-utc", "2026-08-01T00:00:00Z",
        "--uploaded-end-utc", "2026-08-15T00:00:00Z",
        "--as-of-utc", "2026-08-15T00:00:00Z", "--post-run-max-gap-minutes", "45",
    ])
    assert args.command == "post-run-behavior"
    assert args.content_version == 4
    assert args.final_outcome == "Dead"
    assert args.development_build is False
    assert args.post_run_max_gap_minutes == 45
    assert args.as_of_utc.tzinfo is not None
