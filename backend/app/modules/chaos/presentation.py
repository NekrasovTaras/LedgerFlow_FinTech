from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.infrastructure.models import User
from app.modules.auth import current_user

from .application import authorized, get_experiment, start_experiment
from .scenarios import SCENARIOS

router = APIRouter(prefix="/api/v1/chaos", tags=["Chaos Lab"])
Actor = Annotated[User, Depends(current_user)]


class ExperimentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str = Field(min_length=1, max_length=60)


@router.get("/scenarios")
async def scenarios(actor: Actor) -> dict[str, Any]:
    authorized(actor)
    return {
        "items": [scenario.public() for scenario in SCENARIOS.values()],
        "total": len(SCENARIOS),
        "page": 1,
        "page_size": len(SCENARIOS),
    }


@router.post("/experiments", status_code=202)
async def experiment_create(body: ExperimentInput, actor: Actor) -> dict[str, Any]:
    return await start_experiment(actor, body.scenario_id)


@router.get("/experiments/{experiment_id}")
async def experiment_detail(experiment_id: UUID, actor: Actor) -> dict[str, Any]:
    return await get_experiment(actor, str(experiment_id))
