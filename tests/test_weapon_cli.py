from defence_project_analytics.cli import build_parser


def test_weapon_cli_accepts_scope_snapshot_and_time_filters() -> None:
    args = build_parser().parse_args([
        "weapon-performance", "--environment", "Test", "--stage-key", "stage1",
        "--content-version", "2", "--development-build", "false",
        "--segment-ended-start-utc", "2026-08-01T00:00:00Z",
        "--uploaded-end-utc", "2026-08-15T00:00:00Z",
        "--as-of-utc", "2026-08-14T00:00:00Z",
    ])
    assert args.command == "weapon-performance"
    assert args.content_version == 2
    assert args.development_build is False
    assert args.as_of_utc.tzinfo is not None

