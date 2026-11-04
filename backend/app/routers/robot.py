"""Robot page: the address suites connect to, the motion server's state, and its library of models."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..deps import Principal, current_principal, require_manager, require_researcher
from ..services import orchestrator, robot
from ..settings_store import get_setting

router = APIRouter(prefix="/api/v1/robot", tags=["robot"])


def _fail(e: robot.RobotError) -> JSONResponse:
    return JSONResponse(status_code=e.status if e.status >= 400 else 503, content={"error": {"code": e.code, "message": e.message}})


class ModelIn(BaseModel):
    model: str = Field(default="Kimodo-G1-RP-v1", max_length=120)


class InstallIn(BaseModel):
    repo_id: str = Field(max_length=200)


class TestIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)
    duration: float = Field(default=5, ge=0.5, le=60)
    diffusion_steps: int = Field(default=100, ge=1, le=1000)
    model: str = Field(default="Kimodo-G1-RP-v1", max_length=120)
    dof: int = 29
    seed: int | None = None


@router.get("")
def status(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    box = robot.container()
    server = None
    error = None
    if box.get("state") == "running":
        try:
            server = robot.call("GET", "/_admin/status")
        except robot.RobotError as e:
            error = e.message
    token = get_setting(db, "hf_token")
    return {"urls": robot.urls(), "container": box, "server": server, "error": error,
            "library": robot.library(server), "news": robot.newer_on_hub(token), "llama_access": robot.llama_access(token),
            "encoder_repos": list(robot.ENCODER_REPOS)}


@router.post("/start")
def start(request: Request, p: Principal = Depends(require_manager), db: Session = Depends(get_db)):
    try:
        out = orchestrator.start_engine("robot")
    except orchestrator.OrchestratorError as e:
        return _fail(robot.RobotError(str(e), e.status or 503))
    audit(db, "robot.start", actor=p.user, request=request)
    return out


@router.post("/stop")
def stop(request: Request, p: Principal = Depends(require_manager), db: Session = Depends(get_db)):
    try:
        out = orchestrator.stop_engine("robot")
    except orchestrator.OrchestratorError as e:
        return _fail(robot.RobotError(str(e), e.status or 503))
    audit(db, "robot.stop", actor=p.user, request=request)
    return out


@router.post("/load")
def load(body: ModelIn, p: Principal = Depends(require_manager)):
    try:
        return robot.call("POST", "/_admin/load", json={"model": body.model}, timeout=300)
    except robot.RobotError as e:
        return _fail(e)


@router.post("/unload")
def unload(body: ModelIn, p: Principal = Depends(require_manager)):
    try:
        return robot.call("POST", "/_admin/unload", json={"model": body.model}, timeout=60)
    except robot.RobotError as e:
        return _fail(e)


@router.post("/install")
def install(body: InstallIn, request: Request, p: Principal = Depends(require_manager), db: Session = Depends(get_db)):
    repo = body.repo_id.strip()
    if repo not in robot.KNOWN and repo not in robot.INSTALLABLE_EXTRA:
        return _fail(robot.RobotError("This model is not in the library", 400, "not_in_library"))
    token = get_setting(db, "hf_token") if repo == robot.LLAMA else None  # only Llama 3 is gated: the token goes nowhere else
    if repo == robot.LLAMA and robot.llama_access(token)["state"] != "granted":
        return _fail(robot.RobotError("Hugging Face does not give this hub access to Llama 3 yet: follow the steps on this page", 403, "llama_access"))
    try:
        out = robot.call("POST", "/_admin/download", json={"repo_id": repo, **({"token": token} if token else {})})
    except robot.RobotError as e:
        return _fail(e)
    audit(db, "robot.install", actor=p.user, target_type="model", target_id=repo, request=request)
    return out


@router.get("/requests")
def requests_log(p: Principal = Depends(require_manager)):
    try:
        return robot.call("GET", "/_admin/requests")
    except robot.RobotError as e:
        return _fail(e)


@router.post("/test")
def test(body: TestIn, p: Principal = Depends(require_researcher)):
    try:
        result = robot.call("POST", "/generate", json=body.model_dump(exclude_none=True), timeout=600)
    except robot.RobotError as e:
        return _fail(e)
    return robot.summarize_test(result)
