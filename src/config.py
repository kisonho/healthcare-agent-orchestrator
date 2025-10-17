# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict
from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
import yaml

logger = logging.getLogger(__name__)
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


def setup_app_insights_logging(credential, log_level=logging.DEBUG) -> None:
    """Configure OpenTelemetry logging and tracing for Application Insights."""
    os.environ["OTEL_EXPERIMENTAL_RESOURCE_DETECTORS"] = "azure_app_service"
    trace.set_tracer_provider(TracerProvider())
    tracer_provider = trace.get_tracer_provider()

    # Configure Azure Monitor Exporter
    if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        exporter = AzureMonitorTraceExporter(
            connection_string=os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"),
        )
        span_processor = BatchSpanProcessor(exporter)
        tracer_provider.add_span_processor(span_processor)

    # Instrument FastAPI
    FastAPIInstrumentor().instrument()

    # Instrument Logging
    LoggingInstrumentor().instrument(set_logging_format=True)

    # Configure Azure Monitor if connection string is set
    if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        credential = credential
        configure_azure_monitor(
            credential=credential,
            logger=logging.getLogger(__name__),
            connection_string=os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"),
            logging_exporter_enabled=True,
            tracing_exporter_enabled=True,
            metrics_exporter_enabled=True,
            enable_live_metrics=True,
            formatter=formatter
        )

    # Ensure all loggers propagate to root for Azure Monitor
    for name in logging.root.manager.loggerDict:
        logging.getLogger(name).propagate = True


def setup_logging(log_level=logging.DEBUG) -> None:
    # Create a logging handler to write logging records, in OTLP format, to the exporter.
    console_handler = logging.StreamHandler()

    # Add filters to the handler to only process records from semantic_kernel.
    # console_handler.addFilter(logging.Filter("semantic_kernel"))
    console_handler.setFormatter(formatter)

    logger = logging.getLogger()
    logger.addHandler(console_handler)
    logger.setLevel(log_level)


def _load_local_settings() -> Dict[str, Any]:
    """Read local configuration overrides when running without Azure."""
    default_path = Path(__file__).with_name("config.local.yaml")
    settings_path = Path(os.getenv("LOCAL_SETTINGS_PATH", default_path))
    if settings_path.exists():
        with settings_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def load_agent_config(scenario: str) -> dict:
    src_dir = os.path.dirname(os.path.abspath(__file__))
    scenario_directory = os.path.join(src_dir, f"scenarios/{scenario}/config")

    agent_config_path = os.path.join(scenario_directory, "agents.yaml")

    with open(agent_config_path, "r", encoding="utf-8") as f:
        agent_config = yaml.safe_load(f)

    infra_provider = os.getenv("INFRA_PROVIDER", "azure").lower()
    local_settings = _load_local_settings() if infra_provider == "local" else {}

    bot_ids_env = os.getenv("BOT_IDS")
    if bot_ids_env:
        bot_ids = json.loads(bot_ids_env)
    else:
        bot_ids = local_settings.get("bot_ids", {})

    hls_model_env = os.getenv("HLS_MODEL_ENDPOINTS")
    if hls_model_env:
        hls_model_endpoints = json.loads(hls_model_env)
    else:
        hls_model_endpoints = local_settings.get("hls_model_endpoints", {})

    for agent in agent_config:
        agent["bot_id"] = bot_ids.get(agent["name"])
        agent["hls_model_endpoint"] = hls_model_endpoints
        if agent.get("addition_instructions"):
            for file in agent["addition_instructions"]:
                with open(os.path.join(scenario_directory, file)) as f:
                    agent["instructions"] += f.read()

    return agent_config


class DefaultConfig:
    """ Bot Configuration """

    def __init__(self, botId):
        self.APP_ID = botId
        self.APP_PASSWORD = os.environ.get("MicrosoftAppPassword", "")
        self.APP_TYPE = os.environ.get("MicrosoftAppType", "MultiTenant")
        self.APP_TENANTID = os.environ.get("MicrosoftAppTenantId", "")
