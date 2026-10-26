"""Report endpoints and the public report-verification endpoint (PRD 8.9, 8.10)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import reports
from .core import ApiError
from .db import SYSTEM, row, tx
from .deps import Principal, ensure_uuid, ip_hash, public, secured
from .routes_auth import rate_limit

router = APIRouter(prefix="/api/v1", tags=["reports"])


class ReportIn(BaseModel):
    type: str
    format: str = Field(default="CSV", pattern="^(CSV|XLSX|PDF)$")
    academic_year: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")


@router.get("/reports/types")
def types(p: Principal = Depends(secured("reports:request"))):
    items = [{"code": k, "label": v} for k, v in reports.TYPES.items() if (p.institution_id or k not in ("ENROLMENT_EXPORT", "INSTITUTION_PACK"))]
    return {"items": items, "formats": list(reports.FORMATS), "definitions": reports.DEFINITIONS}


@router.post("/reports")
def request_report(body: ReportIn, p: Principal = Depends(secured("reports:request"))):
    return reports.request_report(p, body.type, body.format, body.academic_year)


@router.get("/reports")
def list_reports(p: Principal = Depends(secured("reports:request"))):
    return {"items": reports.list_jobs(p)}


@router.get("/reports/{jid}/download")
def download(jid: str, p: Principal = Depends(secured("reports:request"))):
    path, mime, name = reports.file_for(p, ensure_uuid(jid, "report"))
    return FileResponse(path, media_type=mime, filename=name)


@router.get("/public/reports/{ref}", dependencies=[Depends(public())])
def public_report(ref: str, request: Request):
    """What the QR on a report resolves to: title, generation time, SHA-256 and rule-set version only."""
    rate_limit(f"pubrep:{ip_hash(request)}", 60, 60)
    with tx(SYSTEM) as conn:
        r = row(conn, "SELECT title, generated_at, sha256, rule_set_version, format FROM ops.report_public(:r)", r=ref)
    if not r:
        raise ApiError(404, "NOT_FOUND", "Not found", "No such report reference.")
    return {**r, "note": "Compare this SHA-256 with the file you hold (e.g. `sha256sum`). A hash proves the file is unchanged, not that its content is true."}
