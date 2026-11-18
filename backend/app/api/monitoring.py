"""
app/api/monitoring.py
=====================
Production-grade Infrastructure Monitoring & Observability API.
"""

from __future__ import annotations

from typing import Any, List, Optional
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.schemas.monitoring import (
    AgentStageMetric,
    AIProviderMonitoring,
    ApiPerformanceMetrics,
    DashboardOverviewResponse,
    InfrastructureCard,
    LLMUsageMonitoring,
    RecentSystemEvent,
    ResearchExecutionMonitoring,
    SearchSourceMonitoring,
    StorageMonitoring,
    SystemOverview,
)
from app.security.dependencies import get_optional_user
from app.services.container import get_monitoring_service
from app.services.monitoring_service import MonitoringService

router = APIRouter(prefix="/monitoring", tags=["Monitoring & Observability"])


@router.get(
    "/overview",
    response_model=DashboardOverviewResponse,
    summary="Complete system status, telemetry, infrastructure and usage overview",
)
async def get_dashboard_overview(
    refresh: bool = Query(False, description="Bypass cache and run fresh probe cycle"),
    current_user: Optional[dict] = Depends(get_optional_user),
    service: MonitoringService = Depends(get_monitoring_service),
) -> DashboardOverviewResponse:
    return service.get_dashboard_overview(force_refresh=refresh)


@router.get(
    "/system",
    response_model=SystemOverview,
    summary="System health status, version, uptime and runtime environment",
)
async def get_system_overview(
    service: MonitoringService = Depends(get_monitoring_service),
) -> SystemOverview:
    return service.get_system_overview()


@router.get(
    "/infrastructure",
    response_model=List[InfrastructureCard],
    summary="Core dependencies: FastAPI, PostgreSQL, Redis, Celery, ChromaDB, and Local Storage",
)
async def get_infrastructure_status(
    service: MonitoringService = Depends(get_monitoring_service),
) -> List[InfrastructureCard]:
    return service.get_infrastructure()


@router.get(
    "/providers",
    response_model=List[AIProviderMonitoring],
    summary="Multi-provider LLM status, model routing and probe latency",
)
async def get_provider_monitoring(
    service: MonitoringService = Depends(get_monitoring_service),
) -> List[AIProviderMonitoring]:
    return service.get_providers()


@router.get(
    "/search-sources",
    response_model=List[SearchSourceMonitoring],
    summary="Research retrieval adapters status (arXiv, Wikipedia, DuckDuckGo, Tavily, etc.)",
)
async def get_search_sources_status(
    service: MonitoringService = Depends(get_monitoring_service),
) -> List[SearchSourceMonitoring]:
    return service.get_search_sources()


@router.get(
    "/research-executions",
    response_model=ResearchExecutionMonitoring,
    summary="Real research pipeline execution metrics (active, completed, failed, duration, retries)",
)
async def get_research_executions_monitoring(
    service: MonitoringService = Depends(get_monitoring_service),
) -> ResearchExecutionMonitoring:
    return service.get_research_executions()


@router.get(
    "/agent-metrics",
    response_model=List[AgentStageMetric],
    summary="LangGraph multi-agent node telemetry, duration and execution counts",
)
async def get_agent_metrics(
    service: MonitoringService = Depends(get_monitoring_service),
) -> List[AgentStageMetric]:
    return service.get_agent_metrics()


@router.get(
    "/api-performance",
    response_model=ApiPerformanceMetrics,
    summary="HTTP traffic, requests per minute, error rates, and response latency",
)
async def get_api_performance(
    service: MonitoringService = Depends(get_monitoring_service),
) -> ApiPerformanceMetrics:
    return service.get_api_performance()


@router.get(
    "/usage",
    response_model=LLMUsageMonitoring,
    summary="Aggregated token usage and estimated API cost broken down by provider and model",
)
async def get_llm_usage_monitoring(
    service: MonitoringService = Depends(get_monitoring_service),
) -> LLMUsageMonitoring:
    return service.get_llm_usage()


@router.get(
    "/storage",
    response_model=StorageMonitoring,
    summary="Database, ChromaDB vector collections, report artifacts and persistent disk volume status",
)
async def get_storage_monitoring(
    service: MonitoringService = Depends(get_monitoring_service),
) -> StorageMonitoring:
    return service.get_storage()


@router.get(
    "/events",
    response_model=List[RecentSystemEvent],
    summary="Recent system, pipeline and audit events",
)
async def get_recent_events(
    limit: int = Query(40, ge=1, le=100),
    service: MonitoringService = Depends(get_monitoring_service),
) -> List[RecentSystemEvent]:
    return service.get_recent_events(limit=limit)
