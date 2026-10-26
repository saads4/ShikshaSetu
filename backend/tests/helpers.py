from __future__ import annotations

import uuid

from sqlalchemy import text

from app.claims import current_academic_year
from app.db import owner_tx, scalar


def make_programme(slug: str, code: str, cap: int, *, mode: str = "FULL_TIME", level: str = "UG", bucket: str = "REGULAR") -> str:
    """Create a fresh approved programme with one seat bucket (caps are data, PRD REQ-REF-02). Returns programme id."""
    year = current_academic_year()
    with owner_tx() as conn:
        inst = scalar(conn, "SELECT id FROM ref.institutions WHERE slug=:s", s=slug)
        pid = uuid.uuid4()
        conn.execute(text("INSERT INTO ref.programmes (id, institution_id, programme_code, name, level, discipline, mode, duration_years) "
                          "VALUES (:id,:i,:c,:n,:l,'TEST',:m,4)"), {"id": pid, "i": inst, "c": code, "n": f"Test programme {code}", "l": level, "m": mode})
        conn.execute(text("INSERT INTO core.seat_buckets (id, institution_id, programme_id, academic_year, bucket_type, cap) VALUES (:id,:i,:p,:y,:b,:c)"),
                     {"id": uuid.uuid4(), "i": inst, "p": pid, "y": year, "b": bucket, "c": cap})
    return str(pid)


def bucket_state(slug: str, code: str, bucket: str = "REGULAR") -> dict:
    with owner_tx() as conn:
        r = conn.execute(text("SELECT b.cap, b.occupied, b.approved_extra, b.cumulative_admitted FROM core.seat_buckets b JOIN ref.programmes p ON p.id=b.programme_id "
                              "JOIN ref.institutions i ON i.id=b.institution_id WHERE i.slug=:s AND p.programme_code=:c AND b.bucket_type=:b "
                              "AND b.academic_year=:y"), {"s": slug, "c": code, "b": bucket, "y": current_academic_year()}).mappings().first()
    return dict(r)
