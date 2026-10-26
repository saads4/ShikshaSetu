"""Reference data endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .db import SYSTEM, rows, tx
from .deps import Principal, public, secured

router = APIRouter(prefix="/api/v1/ref", tags=["reference"])

REGIONS = ["NORTH", "SOUTH", "EAST", "WEST", "CENTRAL", "NORTH_EAST"]
INSTITUTION_TYPES = ["ENGINEERING", "POLYTECHNIC", "MANAGEMENT", "PHARMACY", "UNIVERSITY_DEPT"]


@router.get("/lookups", dependencies=[Depends(public())])
def lookups():
    with tx(SYSTEM) as conn:
        buckets = rows(conn, "SELECT code, label, is_supernumerary, description FROM ref.seat_bucket_types ORDER BY code")
        states = rows(conn, "SELECT DISTINCT state, region FROM ref.institutions ORDER BY state")
    return {"regions": REGIONS, "institution_types": INSTITUTION_TYPES, "levels": ["UG", "PG", "DIPLOMA", "PGDM"], "gender": ["M", "F", "O"],
            "category": ["GEN", "OBC", "SC", "ST", "EWS"], "citizenship_type": ["INDIAN", "FOREIGN", "OCI", "PIO"],
            "provisional_types": ["NOGEN", "REFUSED", "FOREIGN"], "bucket_types": buckets, "states": states,
            "withdraw_reasons": ["STUDENT_WITHDREW", "ADMISSION_CANCELLED", "FEE_NOT_PAID", "DATA_ERROR", "OTHER"],
            "assist_reasons": ["NO_PHONE", "PHONE_LOST", "ACCESSIBILITY", "OTHER"]}


@router.get("/institutions")
def institutions(p: Principal = Depends(secured("dashboards:aicte"))):
    from .routes_claims import _in_scope
    with tx(SYSTEM) as conn:
        data = rows(conn, "SELECT id, aicte_id, name, type, state, district, region, status FROM ref.institutions ORDER BY name")
    return {"items": [{**d, "id": str(d["id"])} for d in data if _in_scope(p, d)]}
