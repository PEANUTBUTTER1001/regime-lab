"""찾은 조합 저장 API (P1-9·P1-7, 설계 §5): 목록·저장·불러오기·덮어쓰기/이름 바꾸기·삭제."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from regime_api.errors import ApiError
from regime_api.schemas import PresetCreateIn, PresetUpdateIn, to_engine_dict
from regime_lab.presets import PresetError

router = APIRouter(tags=["presets"])

STATUS = {"validation_failed": 422, "preset_not_found": 404, "search_not_found": 404, "candidate_not_found": 404,
          "name_conflict": 409, "version_conflict": 409}


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PresetError as e:
        raise ApiError(STATUS[e.code], e.code, e.message, e.detail) from None


@router.get("/presets")
def list_presets(request: Request):
    return {"presets": request.app.state.rl.presets.list()}


@router.post("/presets", status_code=201)
def create(req: PresetCreateIn, request: Request):
    s = request.app.state.rl
    if (req.strategy is None) == (req.from_search is None):
        raise ApiError(422, "validation_failed", "Check the input values.",
                       {"fields": {"strategy": "Give either strategy or from_search"}})
    if req.from_search is not None:
        return _call(s.presets.create_from_search, s.jobs.search_dir, req.from_search.search_id,
                     req.from_search.candidate_id, req.name)
    return _call(s.presets.create, req.name, to_engine_dict(req.strategy))


@router.get("/presets/{preset_id}")
def get(preset_id: str, request: Request):
    return _call(request.app.state.rl.presets.get, preset_id)


@router.put("/presets/{preset_id}")
def update(preset_id: str, req: PresetUpdateIn, request: Request):
    strategy = to_engine_dict(req.strategy) if req.strategy is not None else None
    return _call(request.app.state.rl.presets.update, preset_id, req.revision, name=req.name, strategy=strategy)


@router.delete("/presets/{preset_id}", status_code=204)
def delete(preset_id: str, request: Request):
    _call(request.app.state.rl.presets.delete, preset_id)
    return Response(status_code=204)
