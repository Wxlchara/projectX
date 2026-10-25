from fastapi import APIRouter, Request

from app.common.models import Destination, Items, Scenario

router = APIRouter(tags=["rules"])
IMPLEMENTED = {"x-package": 2, "x-implementation": "implemented"}


@router.get("/destinations", response_model=Items[Destination], openapi_extra=IMPLEMENTED)
def destinations(request: Request):
    return Items(items=request.app.state.rule_registry.destinations())


@router.get("/scenarios/{scenario_id}", response_model=Scenario, openapi_extra=IMPLEMENTED)
def scenario(scenario_id: str, request: Request):
    return request.app.state.rule_registry.get_scenario(scenario_id)
