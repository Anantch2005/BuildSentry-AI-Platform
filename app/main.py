
import logging

from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.alerts import router as alerts_router
from app.api.webhook import router as webhook_router
from app.config import settings
from app.database.database import create_tables, engine

try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
except ImportError:  # runtime requirements include the optional instrumentation package
    FastAPIInstrumentor = None

logger = logging.getLogger(__name__)

create_tables()


app = FastAPI(
    title="BuildSentry",

    description=(
        "Guarded failure analysis and "
        "remediation for Jenkins pipelines."
    ),

    version="1.0.0",
)


@app.get("/health")
async def health():

    try:

        with engine.connect() as connection:

            connection.execute(
                text("SELECT 1")
            )

    except SQLAlchemyError as exc:

        raise HTTPException(
            status_code=503,
            detail={
                "status": "unhealthy",
                "service": "buildsentry",
                "database": "disconnected",
                "observability": (
                    "enabled"
                    if settings.otel_enabled and FastAPIInstrumentor is not None
                    else "partially_enabled"
                    if settings.otel_enabled
                    else "disabled"
                ),
            },
        ) from exc


    return {
        "status": "healthy",
        "service": "buildsentry",
        "database": "connected",
        "observability": (
            "enabled"
            if settings.otel_enabled and FastAPIInstrumentor is not None
            else "partially_enabled"
            if settings.otel_enabled
            else "disabled"
        ),
    }


app.include_router(
    webhook_router,
    prefix="/webhook",
)


app.include_router(
    alerts_router,
    prefix="/alerts",
)


if settings.otel_enabled and FastAPIInstrumentor is not None:
    FastAPIInstrumentor.instrument_app(app)
elif settings.otel_enabled:
    logger.warning(
        "FastAPI OpenTelemetry instrumentation is unavailable; HTTP spans are disabled. "
        "Install requirements.txt to enable request instrumentation."
    )