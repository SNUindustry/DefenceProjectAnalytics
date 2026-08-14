from datetime import date

import pandas as pd
import pytest

from defence_project_analytics.bigquery_client import (
    build_query_parameters,
    query_dataframe,
)
from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.models import QueryParameterValue, QuerySpec


class FakeJob:
    def result(self) -> "FakeJob":
        return self

    def to_dataframe(self, *, create_bqstorage_client: bool) -> pd.DataFrame:
        assert create_bqstorage_client is False
        return pd.DataFrame({"ok": [1]})


class FakeClient:
    def __init__(self) -> None:
        self.calls = []

    def query(self, sql, *, job_config, location):
        self.calls.append((sql, job_config, location))
        return FakeJob()


def test_parameter_binding_uses_named_scalar_parameters() -> None:
    parameters = build_query_parameters(
        {
            "environment": "Production",
            "content_version": QueryParameterValue(None, "INT64"),
            "enabled": True,
            "day": date(2026, 8, 14),
        }
    )
    api = {item.name: item.to_api_repr() for item in parameters}
    assert api["environment"]["parameterType"]["type"] == "STRING"
    assert api["content_version"]["parameterType"]["type"] == "INT64"
    assert api["content_version"]["parameterValue"]["value"] is None
    assert api["enabled"]["parameterType"]["type"] == "BOOL"
    assert api["day"]["parameterType"]["type"] == "DATE"


def test_query_dataframe_binds_query_spec() -> None:
    client = FakeClient()
    result = query_dataframe(
        QuerySpec("SELECT @environment", {"environment": "Test"}),
        client=client,
        config=AnalyticsConfig(),
    )
    assert result.to_dict(orient="records") == [{"ok": 1}]
    _, job_config, location = client.calls[0]
    assert location == "asia-northeast3"
    assert job_config.query_parameters[0].name == "environment"
    assert job_config.query_parameters[0].value == "Test"


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE OR REPLACE VIEW `p.d.v` AS SELECT 1",
        "DELETE FROM `p.d.t` WHERE TRUE",
        "SELECT 1; DROP TABLE `p.d.t`",
    ],
)
def test_query_dataframe_rejects_mutation(sql: str) -> None:
    with pytest.raises(ValueError):
        query_dataframe(sql, client=FakeClient(), config=AnalyticsConfig())

