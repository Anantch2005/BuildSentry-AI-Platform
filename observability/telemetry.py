from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
)

from app.config import settings


SERVICE_NAME = "autoheal"
SERVICE_VERSION = "0.6.0"


resource = Resource.create(
    {
        "service.name": SERVICE_NAME,
        "service.version": SERVICE_VERSION,
    }
)


# =========================================================
# TRACING
# =========================================================

trace_provider = TracerProvider(
    resource=resource,
)

trace_exporter = OTLPSpanExporter(
    endpoint=(
        f"{settings.otel_endpoint.rstrip('/')}"
        "/v1/traces"
    ),
)

if settings.otel_enabled:
    trace_provider.add_span_processor(
        BatchSpanProcessor(trace_exporter)
    )

trace.set_tracer_provider(trace_provider)

tracer = trace.get_tracer(
    "autoheal",
    SERVICE_VERSION,
)


# =========================================================
# METRICS
# =========================================================

metric_exporter = OTLPMetricExporter(
    endpoint=(
        f"{settings.otel_endpoint.rstrip('/')}"
        "/v1/metrics"
    ),
)

metric_readers = []
if settings.otel_enabled:
    metric_readers.append(
        PeriodicExportingMetricReader(
            metric_exporter,
            export_interval_millis=15000,
        )
    )

meter_provider = MeterProvider(
    resource=resource,
    metric_readers=metric_readers,
)

metrics.set_meter_provider(
    meter_provider
)

meter = metrics.get_meter(
    "autoheal",
    SERVICE_VERSION,
)


# =========================================================
# METRIC INSTRUMENTS
# =========================================================

incidents_total = meter.create_counter(
    "autoheal_incidents_total",
    description="Total Jenkins incidents received.",
)

ai_classifications_total = meter.create_counter(
    "autoheal_ai_classifications_total",
    description="Total incidents classified by local AI.",
)

policy_decisions_total = meter.create_counter(
    "autoheal_policy_decisions_total",
    description="Total policy decisions.",
)

policy_denials_total = meter.create_counter(
    "autoheal_policy_denials_total",
    description="Total policy denials.",
)

remediation_attempts_total = meter.create_counter(
    "autoheal_remediation_attempts_total",
    description="Total remediation attempts.",
)

remediation_success_total = meter.create_counter(
    "autoheal_remediation_success_total",
    description="Successful remediation attempts.",
)

remediation_failure_total = meter.create_counter(
    "autoheal_remediation_failure_total",
    description="Failed remediation attempts.",
)

healed_total = meter.create_counter(
    "autoheal_healed_total",
    description="Total successfully healed incidents.",
)

escalated_total = meter.create_counter(
    "autoheal_escalated_total",
    description="Total escalated incidents.",
)

processing_duration = meter.create_histogram(
    "autoheal_processing_duration_seconds",
    description="Time spent processing incidents.",
    unit="s",
)