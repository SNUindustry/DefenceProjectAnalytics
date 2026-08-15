from defence_project_analytics.cli import build_parser


def test_progression_cli_accepts_stage_filters_windows_and_snapshot() -> None:
    args = build_parser().parse_args([
        "progression-next-run", "--environment", "Test", "--content-version", "4",
        "--progression-kind", "WeaponRecipe", "--previous-stage-key", "stage1",
        "--next-stage-key", "stage2", "--development-build", "false",
        "--progression-start-utc", "2026-08-01T00:00:00Z",
        "--uploaded-end-utc", "2026-08-15T00:00:00Z",
        "--as-of-utc", "2026-08-15T00:00:00Z",
        "--previous-run-max-gap-minutes", "5", "--next-run-max-gap-minutes", "60",
    ])
    assert args.command == "progression-next-run"
    assert args.content_version == 4
    assert args.development_build is False
    assert args.previous_run_max_gap_minutes == 5
    assert args.next_run_max_gap_minutes == 60
    assert args.as_of_utc.tzinfo is not None

