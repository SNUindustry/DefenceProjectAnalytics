"""User-facing foundation errors."""


class AnalyticsFoundationError(RuntimeError):
    """Base error for expected analytics foundation failures."""


class ADCNotConfiguredError(AnalyticsFoundationError):
    """Application Default Credentials are unavailable."""


ADC_DIAGNOSTIC = """Application Default Credentials (ADC) are not configured.
Install the Google Cloud CLI, then run:
  gcloud auth application-default login
  gcloud config set project bald-ops
No service-account JSON or Unity telemetry upload secret is supported."""

