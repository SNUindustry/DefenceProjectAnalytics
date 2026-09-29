import pytest

from defence_project_analytics.cli import build_parser, main
from defence_project_analytics.config import AnalyticsConfig


def test_approved_backend_profiles_are_physically_distinct():
    production = AnalyticsConfig.for_backend("production")
    test = AnalyticsConfig.for_backend("test")

    assert production.project_id == "bald-ops"
    assert production.backend_environment == "Production"
    assert test.project_id == "bald-ops-test"
    assert test.backend_environment == "Test"
    assert production.project_id != test.project_id


def test_logical_environment_must_match_physical_backend():
    with pytest.raises(ValueError, match="cannot use physical"):
        AnalyticsConfig.for_backend("production").require_environment("Test")
    with pytest.raises(ValueError, match="cannot use physical"):
        AnalyticsConfig.for_backend("test").require_environment("Production")


def test_profile_rejects_identifier_override():
    with pytest.raises(ValueError, match="approved physical backend profile"):
        AnalyticsConfig(
            "bald-ops",
            "game_telemetry",
            "asia-northeast3",
            "Test",
            "test",
        )


def test_cli_exposes_explicit_backend_selector():
    args = build_parser().parse_args([
        "--backend", "test",
        "run-retention",
        "--environment", "Test",
        "--content-version", "1",
    ])
    assert args.backend == "test"


def test_ga_identity_bridge_requires_explicit_backend():
    with pytest.raises(SystemExit) as exc:
        main(["ga-identity-bridge", "--as-of", "2026-09-29T00:00:00Z"])
    assert exc.value.code == 2


def test_observed_uninstall_requires_explicit_backend():
    with pytest.raises(SystemExit) as exc:
        main(["observed-uninstall", "--as-of", "2026-09-29T00:00:00Z"])
    assert exc.value.code == 2


def test_custom_environment_backend_requires_explicit_environment(monkeypatch):
    monkeypatch.setenv("DPA_GCP_PROJECT", "isolated-project")
    monkeypatch.delenv("DPA_BACKEND_ENVIRONMENT", raising=False)
    with pytest.raises(ValueError, match="require DPA_BACKEND_ENVIRONMENT"):
        AnalyticsConfig.from_env()

    monkeypatch.setenv("DPA_BACKEND_ENVIRONMENT", "Test")
    config = AnalyticsConfig.from_env()
    assert config.backend_name == "custom"
    assert config.backend_environment == "Test"
