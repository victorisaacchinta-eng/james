"""HTTP API for the Lab (FastAPI). Thin: every rule lives in service.Lab, so a client that
bypasses the UI still meets the same checks. Serves the no-build frontend from lab/static."""
from __future__ import annotations

import time
from typing import Literal, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from . import settings
from .service import Lab, LabError


class In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewJob(In):
    scenario: str
    asset_id: str
    question: str = Field(max_length=500)


class GuidanceIn(In):
    mode: Optional[Literal["fixture", "local_ai"]] = None


class PrereqIn(In):
    proposal_id: str
    fact: str
    step_id: Optional[str] = None
    reporter: str = "demo-technician"


class AckIn(In):
    proposal_id: str


class ReportIn(In):
    proposal_id: str
    result: Literal["done", "not_done", "unsuccessful", "skipped"]
    note: str = Field(default="", max_length=1000)


class NoteIn(In):
    text: str = Field(max_length=2000)
    corrects: Optional[str] = None


class CloseIn(In):
    outcome: Literal["REPORTED_RESOLVED", "UNRESOLVED", "ESCALATED"]
    summary: str = Field(default="", max_length=2000)


class ReviewIn(In):
    decision: Literal["approve", "reject", "revoke"]
    reason: str = Field(max_length=1000)
    reviewer: str = "demo-reviewer"


def create_app(lab: Optional[Lab] = None) -> FastAPI:
    lab = lab or Lab()
    app = FastAPI(title="JAMES Workflow Lab", version="0.1.0", docs_url="/api/docs")
    app.state.lab = lab

    @app.exception_handler(LabError)
    def _lab_error(_: Request, e: LabError):
        return JSONResponse(status_code=e.status, content={"error": e.message, **e.detail})

    @app.middleware("http")
    async def _timing(request: Request, call_next):
        t0 = time.perf_counter()
        resp = await call_next(request)
        resp.headers["X-Elapsed-Ms"] = f"{(time.perf_counter() - t0) * 1000:.1f}"
        return resp

    def role(x_demo_role: Optional[str], need: str):
        """Demo roles, not authentication: this only keeps the demo honest about who does what."""
        if (x_demo_role or "technician") != need:
            raise LabError(403, f"switch to the demo {need} role for this ({settings.ROLES_LABEL})")

    @app.get("/api/meta")
    def meta():
        return {"synthetic_label": settings.SYNTHETIC_LABEL, "roles_label": settings.ROLES_LABEL,
                "mode": settings.MODE, "fixture_label": settings.FIXTURE_LABEL,
                "packs": {k: p.hud_label for k, p in lab.packs.items()},
                "assets": {k: {"label": v["label"], "category": v["category"], "pack": v.get("pack")}
                           for k, v in lab.meta.items()}}

    @app.get("/api/scenarios")
    def scenarios():
        return lab.scenarios()

    @app.get("/api/jobs")
    def jobs():
        return [lab.job_view(j["job_id"]) for j in lab.store.jobs()]

    @app.post("/api/jobs")
    def new_job(b: NewJob, x_demo_role: Optional[str] = Header(None)):
        role(x_demo_role, "technician")
        return lab.create_job(b.scenario, b.asset_id, b.question)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return lab.job_view(job_id)

    @app.get("/api/jobs/{job_id}/evidence")
    def evidence(job_id: str):
        return lab.evidence(job_id)

    @app.post("/api/jobs/{job_id}/guidance")
    def guidance(job_id: str, b: GuidanceIn, x_demo_role: Optional[str] = Header(None)):
        role(x_demo_role, "technician")
        return lab.guidance(job_id, b.mode)

    @app.get("/api/jobs/{job_id}/steps")
    def steps(job_id: str):
        return lab.steps(job_id)

    @app.post("/api/jobs/{job_id}/prerequisites")
    def prereq(job_id: str, b: PrereqIn, x_demo_role: Optional[str] = Header(None),
               idempotency_key: Optional[str] = Header(None)):
        role(x_demo_role, "technician")
        return lab.record_prerequisite(job_id, b.proposal_id, b.fact, b.reporter, b.step_id, idempotency_key)

    @app.post("/api/jobs/{job_id}/steps/{step_id}/acknowledge")
    def ack(job_id: str, step_id: str, b: AckIn, x_demo_role: Optional[str] = Header(None),
            idempotency_key: Optional[str] = Header(None)):
        role(x_demo_role, "technician")
        return lab.acknowledge(job_id, step_id, b.proposal_id, idempotency_key)

    @app.post("/api/jobs/{job_id}/steps/{step_id}/report")
    def report(job_id: str, step_id: str, b: ReportIn, x_demo_role: Optional[str] = Header(None),
               idempotency_key: Optional[str] = Header(None)):
        role(x_demo_role, "technician")
        return lab.report(job_id, step_id, b.proposal_id, b.result, b.note, idempotency_key)

    @app.post("/api/jobs/{job_id}/notes")
    def note(job_id: str, b: NoteIn, idempotency_key: Optional[str] = Header(None)):
        return lab.add_note(job_id, b.text, b.corrects, idempotency_key)

    @app.post("/api/jobs/{job_id}/close")
    def close(job_id: str, b: CloseIn, x_demo_role: Optional[str] = Header(None),
              idempotency_key: Optional[str] = Header(None)):
        role(x_demo_role, "technician")
        return lab.close(job_id, b.outcome, b.summary, idempotency_key)

    @app.post("/api/jobs/{job_id}/reviews")
    def review(job_id: str, b: ReviewIn, x_demo_role: Optional[str] = Header(None),
               idempotency_key: Optional[str] = Header(None)):
        role(x_demo_role, "reviewer")
        return lab.review(job_id, b.decision, b.reason, b.reviewer, idempotency_key)

    @app.get("/api/jobs/{job_id}/events")
    def events(job_id: str):
        return lab.events(job_id)

    @app.get("/api/jobs/{job_id}/export")
    def export(job_id: str, format: Literal["markdown", "json"] = "json"):
        text, ctype = lab.export(job_id, "json" if format == "json" else "markdown")
        ext = "json" if format == "json" else "md"
        return PlainTextResponse(text, media_type=ctype,
                                 headers={"Content-Disposition": f'inline; filename="{job_id}.{ext}"'})

    @app.get("/api/history/search")
    def history(asset_id: str, q: str = ""):
        rec, other = lab.history(asset_id, q)
        return {"recommended": [r.model_dump() for r in rec], "not_recommended": [r.model_dump() for r in other]}

    @app.get("/api/sources")
    def source(ref: str):
        return lab.source(ref)

    @app.get("/api/developer")
    def developer():
        return lab.developer()

    @app.get("/")
    def index():
        return FileResponse(settings.STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=settings.STATIC), name="static")
    return app
