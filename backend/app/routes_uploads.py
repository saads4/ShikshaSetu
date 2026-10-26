"""Bulk upload endpoints (PRD 8.5)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from . import uploads
from .claims import current_academic_year
from .db import rows, tx
from .deps import Principal, ensure_uuid, secured
from .routes_claims import _require_institution

router = APIRouter(prefix="/api/v1", tags=["uploads"])


class JobIn(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    academic_year: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")


@router.get("/templates/enrolment/latest")
def template(p: Principal = Depends(secured("uploads:write"))):
    return uploads.template()


@router.get("/templates/enrolment/latest.csv", response_class=PlainTextResponse)
def template_csv(p: Principal = Depends(secured("uploads:write"))):
    return PlainTextResponse(uploads.template()["csv_header"] + "\n", media_type="text/csv", headers={"Content-Disposition": "attachment; filename=enrolment-template-v1.csv"})


@router.post("/upload-jobs")
def create_job(body: JobIn, p: Principal = Depends(secured("uploads:write"))):
    _require_institution(p)
    return uploads.create_job(p, body.filename, body.academic_year)


@router.put("/upload-jobs/{job_id}/content")
async def put_content(job_id: str, request: Request, p: Principal = Depends(secured("uploads:write"))):
    _require_institution(p)
    data = await request.body()
    return uploads.store_content(p, ensure_uuid(job_id, "job"), data)


@router.get("/upload-jobs")
def list_jobs(p: Principal = Depends(secured("uploads:write")), limit: int = Query(default=30, ge=1, le=100)):
    with tx(p.db_ctx()) as conn:
        data = rows(conn, "SELECT j.id, j.filename, j.status, j.total_rows, j.accepted, j.held, j.rejected, j.noop, j.committed, j.failed, j.created_at, u.username AS created_by_name "
                          "FROM ingest.upload_jobs j JOIN iam.users u ON u.id=j.created_by WHERE j.institution_id=CAST(:i AS uuid) ORDER BY j.created_at DESC LIMIT :n",
                    i=_require_institution(p), n=limit)
    return {"items": [{**d, "id": str(d["id"])} for d in data]}


@router.get("/upload-jobs/{job_id}")
def get_job(job_id: str, p: Principal = Depends(secured("uploads:write"))):
    _require_institution(p)
    return uploads.job_view(p, ensure_uuid(job_id, "job"))


@router.get("/upload-jobs/{job_id}/rows")
def job_rows(job_id: str, p: Principal = Depends(secured("uploads:write")), outcome: str | None = None, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    _require_institution(p)
    return uploads.rows_view(p, ensure_uuid(job_id, "job"), outcome, limit, offset)


@router.get("/upload-jobs/{job_id}/errors.csv", response_class=PlainTextResponse)
def job_errors(job_id: str, p: Principal = Depends(secured("uploads:write"))):
    _require_institution(p)
    return PlainTextResponse(uploads.errors_csv(p, ensure_uuid(job_id, "job")), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=errors.csv"})


@router.post("/upload-jobs/{job_id}/commit")
def commit(job_id: str, p: Principal = Depends(secured("uploads:commit"))):
    _require_institution(p)
    return uploads.commit(p, ensure_uuid(job_id, "job"))


@router.post("/upload-jobs/{job_id}/cancel")
def cancel(job_id: str, p: Principal = Depends(secured("uploads:write"))):
    _require_institution(p)
    return uploads.cancel(p, ensure_uuid(job_id, "job"))
