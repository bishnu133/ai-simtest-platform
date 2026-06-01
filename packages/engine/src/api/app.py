"""
FastAPI Application - REST API for the AI SimTest platform.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src.core.config import settings
from src.core.logging import get_logger, setup_logging
from src.core.orchestrator import SimulationOrchestrator
from src.models import (
    BotConfig,
    JudgeConfig,
    Persona,
    SimulationConfig,
    SimulationStatus,
)

logger = get_logger(__name__)

# Track active simulations
_simulations: dict[str, SimulationOrchestrator] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("api_starting", env=settings.app_env)
    yield
    logger.info("api_shutting_down")


app = FastAPI(
    title="AI SimTest",
    description="Open-source AI simulation testing platform",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Request / Response Models
# ============================================================

class CreateSimulationRequest(BaseModel):
    """Request to create and run a new simulation."""
    name: str = "Untitled Simulation"
    bot_endpoint: str
    bot_api_key: str | None = None
    bot_request_format: str = "openai"
    bot_response_path: str = "choices.0.message.content"
    documentation: str = ""
    success_criteria: list[str] = Field(default_factory=list)
    num_personas: int = 20
    max_turns: int = 15
    max_parallel: int = 10
    persona_generator_model: str = "gpt-4-turbo"
    user_simulator_model: str = "gpt-4-turbo"
    judges: list[JudgeConfig] | None = None
    custom_personas: list[dict[str, Any]] | None = None


class SimulationStatusResponse(BaseModel):
    simulation_id: str
    status: str
    personas_count: int = 0
    conversations_count: int = 0
    error: str | None = None


class SimulationReportResponse(BaseModel):
    simulation_id: str
    status: str
    summary: dict[str, Any] | None = None
    score_by_judge: dict[str, float] | None = None
    score_by_persona_type: dict[str, float] | None = None
    recommendations: list[str] | None = None
    failure_patterns: list[dict[str, Any]] | None = None


# ============================================================
# Endpoints
# ============================================================

@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.post("/simulations", response_model=SimulationStatusResponse)
async def create_simulation(req: CreateSimulationRequest):
    """Create and start a new simulation run."""
    config = SimulationConfig(
        name=req.name,
        bot=BotConfig(
            api_endpoint=req.bot_endpoint,
            api_key=req.bot_api_key,
            request_format=req.bot_request_format,
            response_path=req.bot_response_path,
        ),
        documentation=req.documentation,
        success_criteria=req.success_criteria,
        num_personas=req.num_personas,
        max_turns_per_conversation=req.max_turns,
        max_parallel_conversations=req.max_parallel,
        persona_generator_model=req.persona_generator_model,
        user_simulator_model=req.user_simulator_model,
        judges=req.judges or [
            JudgeConfig(name="grounding", weight=0.30),
            JudgeConfig(name="safety", weight=0.30),
            JudgeConfig(name="quality", weight=0.20),
            JudgeConfig(name="relevance", weight=0.20),
        ],
    )

    orchestrator = SimulationOrchestrator(config)
    _simulations[config.id] = orchestrator

    # Parse custom personas if provided
    custom_personas = None
    if req.custom_personas:
        custom_personas = [Persona(**p) for p in req.custom_personas]

    # Run simulation in background
    asyncio.create_task(_run_simulation_bg(config.id, orchestrator, custom_personas))

    return SimulationStatusResponse(
        simulation_id=config.id,
        status=SimulationStatus.PENDING.value,
    )


@app.get("/simulations/{sim_id}/status", response_model=SimulationStatusResponse)
async def get_simulation_status(sim_id: str):
    """Get the current status of a simulation."""
    orch = _simulations.get(sim_id)
    if not orch:
        raise HTTPException(status_code=404, detail="Simulation not found")

    return SimulationStatusResponse(
        simulation_id=sim_id,
        status=orch.run.status.value,
        personas_count=len(orch.run.personas),
        conversations_count=len(orch.run.conversations),
        error=orch.run.error_message,
    )


@app.get("/simulations/{sim_id}/report", response_model=SimulationReportResponse)
async def get_simulation_report(sim_id: str):
    """Get the simulation report (available after completion)."""
    orch = _simulations.get(sim_id)
    if not orch:
        raise HTTPException(status_code=404, detail="Simulation not found")

    if orch.run.status != SimulationStatus.COMPLETED:
        return SimulationReportResponse(
            simulation_id=sim_id,
            status=orch.run.status.value,
        )

    report = orch.run.report
    return SimulationReportResponse(
        simulation_id=sim_id,
        status="completed",
        summary=report.summary.model_dump(mode="json") if report else None,
        score_by_judge=report.score_by_judge if report else None,
        score_by_persona_type=report.score_by_persona_type if report else None,
        recommendations=report.recommendations if report else None,
        failure_patterns=[fp.model_dump() for fp in report.failure_patterns] if report else None,
    )


class ExportRequest(BaseModel):
    formats: list[str] = Field(default_factory=lambda: ["jsonl", "csv", "summary"])


@app.post("/simulations/{sim_id}/export")
async def export_simulation(sim_id: str, req: ExportRequest | None = None):
    """Export simulation results in various formats."""
    orch = _simulations.get(sim_id)
    if not orch:
        raise HTTPException(status_code=404, detail="Simulation not found")

    if orch.run.status != SimulationStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Simulation not yet completed")

    formats = req.formats if req else ["jsonl", "csv", "summary"]
    exported = orch.export_results(
        output_dir=f"./reports/{sim_id}",
        formats=formats,
    )
    return {"exported_files": exported}


@app.get("/simulations/{sim_id}/personas")
async def get_personas(sim_id: str):
    """Get the generated personas for a simulation."""
    orch = _simulations.get(sim_id)
    if not orch:
        raise HTTPException(status_code=404, detail="Simulation not found")

    return {
        "personas": [p.model_dump() for p in orch.run.personas],
        "count": len(orch.run.personas),
    }


@app.get("/simulations")
async def list_simulations():
    """List all simulations."""
    return {
        "simulations": [
            {
                "id": sid,
                "name": orch.config.name,
                "status": orch.run.status.value,
                "personas": len(orch.run.personas),
                "conversations": len(orch.run.conversations),
            }
            for sid, orch in _simulations.items()
        ]
    }


# ============================================================
# Background Tasks
# ============================================================

async def _run_simulation_bg(
    sim_id: str,
    orchestrator: SimulationOrchestrator,
    personas: list[Persona] | None = None,
) -> None:
    """Run simulation in background."""
    try:
        await orchestrator.run_simulation(personas=personas)
        logger.info("simulation_bg_complete", sim_id=sim_id)
    except Exception as e:
        logger.error("simulation_bg_failed", sim_id=sim_id, error=str(e))