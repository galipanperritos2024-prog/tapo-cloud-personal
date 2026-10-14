"""EXPERIMENTAL REST endpoints, mounted under ``/api/lab`` only when enabled.

Same authentication/CSRF as the rest of the API. Every response carries the
``X-Experimental: tapo-lab`` header. Credentials in requests are used for a
single call and never persisted.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from tapo_lab import discovery
from tapo_lab.device import adapter
from tapo_lab.guard import LabDisabled, NotOwnDevice
from tapo_lab.protocol import redact, to_note
from tapocloud.api.deps import current_user


def _mark(response: Response) -> None:
    response.headers["X-Experimental"] = "tapo-lab"


router = APIRouter(prefix="/api/lab", tags=["tapo-lab (EXPERIMENTAL)"], dependencies=[Depends(current_user), Depends(_mark)])


class LabCall(BaseModel):
    feature: str = Field(max_length=64)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    args: list[Any] = Field(default_factory=list, max_length=4)
    as_note: bool = False


def _errors(exc: Exception) -> HTTPException:
    if isinstance(exc, LabDisabled):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, NotOwnDevice):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


@router.get("/features")
def features() -> list[dict[str, Any]]:
    return adapter.list_features()


@router.get("/cameras/{camera_id}/interfaces")
async def interfaces(camera_id: int) -> dict[str, Any]:
    try:
        return await run_in_threadpool(discovery.interfaces, camera_id)
    except Exception as exc:
        raise _errors(exc) from exc


@router.post("/cameras/{camera_id}/call")
async def lab_call(camera_id: int, body: LabCall) -> dict[str, Any]:
    try:
        result = await run_in_threadpool(adapter.call, camera_id, body.feature, body.username, body.password, body.args)
    except Exception as exc:
        raise _errors(exc) from exc
    out: dict[str, Any] = {"feature": body.feature, "result": redact(result), "experimental": True}
    if body.as_note:
        out["markdown"] = to_note(body.feature, result)
    return out
