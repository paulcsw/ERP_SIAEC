"""Reference Data CRUD + CSV Import (§8.6) — Aircraft, Work Packages, Shop Streams."""
import csv
import io

from fastapi import APIRouter, Depends, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_role
from app.models.reference import Aircraft, ShopStream, WorkPackage
from app.schemas.common import APIError, PaginatedResponse, pagination_params
from app.schemas.reference import (
    AircraftCreate,
    AircraftResponse,
    AircraftUpdate,
    ShopStreamCreate,
    ShopStreamResponse,
    ShopStreamUpdate,
    WorkPackageCreate,
    WorkPackageResponse,
    WorkPackageUpdate,
)
from app.services.audit_service import write_audit

router = APIRouter(prefix="/api", tags=["reference"])

VALID_STATUSES = {"ACTIVE", "COMPLETED", "ON_HOLD", "CANCELLED"}


def _validate_status(status: str) -> None:
    if status not in VALID_STATUSES:
        raise APIError(
            422,
            f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}",
            "VALIDATION_ERROR",
            field="status",
        )


# ═══════════════════════════════════════════════════════════════════
# Aircraft
# ═══════════════════════════════════════════════════════════════════

@router.get("/aircraft", response_model=PaginatedResponse[AircraftResponse])
async def list_aircraft(
    paging: dict = Depends(pagination_params),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    total = (
        await db.execute(select(func.count()).select_from(Aircraft))
    ).scalar()
    rows = (
        await db.execute(
            select(Aircraft).order_by(Aircraft.id)
            .offset(paging["offset"]).limit(paging["per_page"])
        )
    ).scalars().all()
    return {
        "items": [AircraftResponse.model_validate(r, from_attributes=True) for r in rows],
        "total": total,
        "page": paging["page"],
        "per_page": paging["per_page"],
    }


@router.post("/aircraft", response_model=AircraftResponse, status_code=201)
async def create_aircraft(
    body: AircraftCreate,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    _validate_status(body.status)

    dup = (
        await db.execute(select(Aircraft).where(Aircraft.ac_reg == body.ac_reg))
    ).scalar_one_or_none()
    if dup:
        raise APIError(422, f"ac_reg '{body.ac_reg}' already exists", "VALIDATION_ERROR", field="ac_reg")

    obj = Aircraft(ac_reg=body.ac_reg, airline=body.airline, status=body.status)
    db.add(obj)
    await db.flush()

    await write_audit(
        db, actor_id=current_user["user_id"],
        entity_type="aircraft", entity_id=obj.id, action="CREATE",
        after=AircraftResponse.model_validate(obj, from_attributes=True).model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(obj)
    return AircraftResponse.model_validate(obj, from_attributes=True)


@router.patch("/aircraft/{aircraft_id}", response_model=AircraftResponse)
async def update_aircraft(
    aircraft_id: int,
    body: AircraftUpdate,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    obj = (
        await db.execute(select(Aircraft).where(Aircraft.id == aircraft_id))
    ).scalar_one_or_none()
    if not obj:
        raise APIError(404, "Aircraft not found", "NOT_FOUND")

    before = AircraftResponse.model_validate(obj, from_attributes=True).model_dump(mode="json")

    if body.airline is not None:
        obj.airline = body.airline
    if body.status is not None:
        _validate_status(body.status)
        obj.status = body.status

    await db.flush()
    await write_audit(
        db, actor_id=current_user["user_id"],
        entity_type="aircraft", entity_id=obj.id, action="UPDATE",
        before=before,
        after=AircraftResponse.model_validate(obj, from_attributes=True).model_dump(mode="json"),
    )
    await db.commit()
    return AircraftResponse.model_validate(obj, from_attributes=True)


# ═══════════════════════════════════════════════════════════════════
# Work Packages
# ═══════════════════════════════════════════════════════════════════

@router.get("/work-packages", response_model=PaginatedResponse[WorkPackageResponse])
async def list_work_packages(
    paging: dict = Depends(pagination_params),
    aircraft_id: int | None = Query(None),
    rfo_no: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(WorkPackage)
    cq = select(func.count()).select_from(WorkPackage)

    if aircraft_id is not None:
        q = q.where(WorkPackage.aircraft_id == aircraft_id)
        cq = cq.where(WorkPackage.aircraft_id == aircraft_id)
    if rfo_no is not None:
        q = q.where(WorkPackage.rfo_no == rfo_no)
        cq = cq.where(WorkPackage.rfo_no == rfo_no)

    total = (await db.execute(cq)).scalar()
    rows = (
        await db.execute(
            q.order_by(WorkPackage.id)
            .offset(paging["offset"]).limit(paging["per_page"])
        )
    ).scalars().all()
    return {
        "items": [WorkPackageResponse.model_validate(r, from_attributes=True) for r in rows],
        "total": total,
        "page": paging["page"],
        "per_page": paging["per_page"],
    }


@router.post("/work-packages", response_model=WorkPackageResponse, status_code=201)
async def create_work_package(
    body: WorkPackageCreate,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    # Validate aircraft FK
    ac = (
        await db.execute(select(Aircraft).where(Aircraft.id == body.aircraft_id))
    ).scalar_one_or_none()
    if not ac:
        raise APIError(422, f"aircraft_id {body.aircraft_id} not found", "VALIDATION_ERROR", field="aircraft_id")

    # Duplicate rfo_no check (only when non-null)
    if body.rfo_no:
        dup = (
            await db.execute(select(WorkPackage).where(WorkPackage.rfo_no == body.rfo_no))
        ).scalar_one_or_none()
        if dup:
            raise APIError(422, f"rfo_no '{body.rfo_no}' already exists", "VALIDATION_ERROR", field="rfo_no")

    obj = WorkPackage(
        aircraft_id=body.aircraft_id,
        rfo_no=body.rfo_no,
        title=body.title,
        start_date=body.start_date,
        end_date=body.end_date,
        priority=body.priority,
    )
    db.add(obj)
    await db.flush()

    await write_audit(
        db, actor_id=current_user["user_id"],
        entity_type="work_package", entity_id=obj.id, action="CREATE",
        after=WorkPackageResponse.model_validate(obj, from_attributes=True).model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(obj)
    return WorkPackageResponse.model_validate(obj, from_attributes=True)


@router.patch("/work-packages/{wp_id}", response_model=WorkPackageResponse)
async def update_work_package(
    wp_id: int,
    body: WorkPackageUpdate,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    obj = (
        await db.execute(select(WorkPackage).where(WorkPackage.id == wp_id))
    ).scalar_one_or_none()
    if not obj:
        raise APIError(404, "Work package not found", "NOT_FOUND")

    before = WorkPackageResponse.model_validate(obj, from_attributes=True).model_dump(mode="json")

    if body.rfo_no is not None:
        # Dup check
        dup = (
            await db.execute(
                select(WorkPackage).where(WorkPackage.rfo_no == body.rfo_no, WorkPackage.id != wp_id)
            )
        ).scalar_one_or_none()
        if dup:
            raise APIError(422, f"rfo_no '{body.rfo_no}' already exists", "VALIDATION_ERROR", field="rfo_no")
        obj.rfo_no = body.rfo_no
    if body.title is not None:
        obj.title = body.title
    if body.start_date is not None:
        obj.start_date = body.start_date
    if body.end_date is not None:
        obj.end_date = body.end_date
    if body.priority is not None:
        obj.priority = body.priority
    if body.status is not None:
        _validate_status(body.status)
        obj.status = body.status

    await db.flush()
    await write_audit(
        db, actor_id=current_user["user_id"],
        entity_type="work_package", entity_id=obj.id, action="UPDATE",
        before=before,
        after=WorkPackageResponse.model_validate(obj, from_attributes=True).model_dump(mode="json"),
    )
    await db.commit()
    return WorkPackageResponse.model_validate(obj, from_attributes=True)


# ═══════════════════════════════════════════════════════════════════
# Shop Streams
# ═══════════════════════════════════════════════════════════════════

@router.get("/shop-streams", response_model=PaginatedResponse[ShopStreamResponse])
async def list_shop_streams(
    paging: dict = Depends(pagination_params),
    work_package_id: int | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(ShopStream)
    cq = select(func.count()).select_from(ShopStream)

    if work_package_id is not None:
        q = q.where(ShopStream.work_package_id == work_package_id)
        cq = cq.where(ShopStream.work_package_id == work_package_id)

    total = (await db.execute(cq)).scalar()
    rows = (
        await db.execute(
            q.order_by(ShopStream.id)
            .offset(paging["offset"]).limit(paging["per_page"])
        )
    ).scalars().all()
    return {
        "items": [ShopStreamResponse.model_validate(r, from_attributes=True) for r in rows],
        "total": total,
        "page": paging["page"],
        "per_page": paging["per_page"],
    }


@router.post("/shop-streams", response_model=ShopStreamResponse, status_code=201)
async def create_shop_stream(
    body: ShopStreamCreate,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    _validate_status(body.status)

    # Validate WP FK
    wp = (
        await db.execute(select(WorkPackage).where(WorkPackage.id == body.work_package_id))
    ).scalar_one_or_none()
    if not wp:
        raise APIError(422, f"work_package_id {body.work_package_id} not found", "VALIDATION_ERROR", field="work_package_id")

    # Duplicate (work_package_id, shop_code) check
    dup = (
        await db.execute(
            select(ShopStream).where(
                ShopStream.work_package_id == body.work_package_id,
                ShopStream.shop_code == body.shop_code,
            )
        )
    ).scalar_one_or_none()
    if dup:
        raise APIError(
            422,
            f"shop_code '{body.shop_code}' already exists for this work package",
            "VALIDATION_ERROR",
            field="shop_code",
        )

    obj = ShopStream(
        work_package_id=body.work_package_id,
        shop_code=body.shop_code,
        status=body.status,
    )
    db.add(obj)
    await db.flush()

    await write_audit(
        db, actor_id=current_user["user_id"],
        entity_type="shop_stream", entity_id=obj.id, action="CREATE",
        after=ShopStreamResponse.model_validate(obj, from_attributes=True).model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(obj)
    return ShopStreamResponse.model_validate(obj, from_attributes=True)


@router.patch("/shop-streams/{ss_id}", response_model=ShopStreamResponse)
async def update_shop_stream(
    ss_id: int,
    body: ShopStreamUpdate,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    obj = (
        await db.execute(select(ShopStream).where(ShopStream.id == ss_id))
    ).scalar_one_or_none()
    if not obj:
        raise APIError(404, "Shop stream not found", "NOT_FOUND")

    before = ShopStreamResponse.model_validate(obj, from_attributes=True).model_dump(mode="json")

    if body.shop_code is not None:
        # Dup check
        dup = (
            await db.execute(
                select(ShopStream).where(
                    ShopStream.work_package_id == obj.work_package_id,
                    ShopStream.shop_code == body.shop_code,
                    ShopStream.id != ss_id,
                )
            )
        ).scalar_one_or_none()
        if dup:
            raise APIError(
                422,
                f"shop_code '{body.shop_code}' already exists for this work package",
                "VALIDATION_ERROR",
                field="shop_code",
            )
        obj.shop_code = body.shop_code
    if body.status is not None:
        _validate_status(body.status)
        obj.status = body.status

    await db.flush()
    await write_audit(
        db, actor_id=current_user["user_id"],
        entity_type="shop_stream", entity_id=obj.id, action="UPDATE",
        before=before,
        after=ShopStreamResponse.model_validate(obj, from_attributes=True).model_dump(mode="json"),
    )
    await db.commit()
    return ShopStreamResponse.model_validate(obj, from_attributes=True)


# ═══════════════════════════════════════════════════════════════════
# CSV Import (§8.6.3)
# ═══════════════════════════════════════════════════════════════════

MAX_CSV_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_ENTITY_TYPES = {"aircraft", "work_package", "shop_stream"}


@router.post("/reference/import/csv")
async def import_csv(
    file: UploadFile,
    entity_type: str = Query(...),
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    if entity_type not in ALLOWED_ENTITY_TYPES:
        raise APIError(
            422,
            f"entity_type must be one of: {', '.join(sorted(ALLOWED_ENTITY_TYPES))}",
            "VALIDATION_ERROR",
            field="entity_type",
        )

    raw = await file.read()
    if len(raw) > MAX_CSV_SIZE:
        raise APIError(422, "File exceeds 5 MB limit", "VALIDATION_ERROR", field="file")

    text_content = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text_content))

    created_count = 0
    skipped_count = 0
    errors: list[dict] = []

    if entity_type == "aircraft":
        created_count, skipped_count, errors = await _import_aircraft(
            reader, db, current_user["user_id"]
        )
    elif entity_type == "work_package":
        created_count, skipped_count, errors = await _import_work_packages(
            reader, db, current_user["user_id"]
        )
    elif entity_type == "shop_stream":
        created_count, skipped_count, errors = await _import_shop_streams(
            reader, db, current_user["user_id"]
        )

    await db.commit()

    return {
        "entity_type": entity_type,
        "created_count": created_count,
        "skipped_count": skipped_count,
        "errors": errors,
    }


async def _import_aircraft(
    reader: csv.DictReader, db: AsyncSession, actor_id: int
) -> tuple[int, int, list[dict]]:
    created, skipped, errors = 0, 0, []
    for i, row in enumerate(reader, start=2):  # row 1 = header
        ac_reg = (row.get("ac_reg") or "").strip()
        if not ac_reg:
            errors.append({"row": i, "reason": "ac_reg is required"})
            continue

        dup = (
            await db.execute(select(Aircraft).where(Aircraft.ac_reg == ac_reg))
        ).scalar_one_or_none()
        if dup:
            skipped += 1
            continue

        status = (row.get("status") or "ACTIVE").strip()
        if status not in VALID_STATUSES:
            errors.append({"row": i, "reason": f"Invalid status '{status}'"})
            continue

        obj = Aircraft(
            ac_reg=ac_reg,
            airline=(row.get("airline") or "").strip() or None,
            status=status,
        )
        db.add(obj)
        await db.flush()
        await write_audit(
            db, actor_id=actor_id,
            entity_type="aircraft", entity_id=obj.id, action="CREATE",
            after={"ac_reg": obj.ac_reg, "airline": obj.airline, "status": obj.status},
        )
        created += 1

    return created, skipped, errors


async def _import_work_packages(
    reader: csv.DictReader, db: AsyncSession, actor_id: int
) -> tuple[int, int, list[dict]]:
    created, skipped, errors = 0, 0, []
    for i, row in enumerate(reader, start=2):
        ac_reg = (row.get("aircraft_ac_reg") or "").strip()
        title = (row.get("title") or "").strip()
        if not ac_reg or not title:
            errors.append({"row": i, "reason": "aircraft_ac_reg and title are required"})
            continue

        ac = (
            await db.execute(select(Aircraft).where(Aircraft.ac_reg == ac_reg))
        ).scalar_one_or_none()
        if not ac:
            errors.append({"row": i, "reason": f"aircraft_ac_reg '{ac_reg}' not found"})
            continue

        rfo_no = (row.get("rfo_no") or "").strip() or None
        # Skip if rfo_no duplicate
        if rfo_no:
            dup = (
                await db.execute(select(WorkPackage).where(WorkPackage.rfo_no == rfo_no))
            ).scalar_one_or_none()
            if dup:
                skipped += 1
                continue

        priority_str = (row.get("priority") or "0").strip()
        try:
            priority = int(priority_str)
        except ValueError:
            priority = 0

        from datetime import date as _date

        start_date = _parse_date(row.get("start_date"))
        end_date = _parse_date(row.get("end_date"))

        obj = WorkPackage(
            aircraft_id=ac.id,
            rfo_no=rfo_no,
            title=title,
            start_date=start_date,
            end_date=end_date,
            priority=priority,
        )
        db.add(obj)
        await db.flush()
        await write_audit(
            db, actor_id=actor_id,
            entity_type="work_package", entity_id=obj.id, action="CREATE",
            after={"rfo_no": obj.rfo_no, "title": obj.title, "aircraft_id": obj.aircraft_id},
        )
        created += 1

    return created, skipped, errors


async def _import_shop_streams(
    reader: csv.DictReader, db: AsyncSession, actor_id: int
) -> tuple[int, int, list[dict]]:
    created, skipped, errors = 0, 0, []
    for i, row in enumerate(reader, start=2):
        shop_code = (row.get("shop_code") or "").strip()
        if not shop_code:
            errors.append({"row": i, "reason": "shop_code is required"})
            continue

        # Resolve work package by rfo_no or title
        wp_rfo = (row.get("work_package_rfo_no") or "").strip()
        wp_title = (row.get("title") or "").strip()
        wp = None
        if wp_rfo:
            wp = (
                await db.execute(select(WorkPackage).where(WorkPackage.rfo_no == wp_rfo))
            ).scalar_one_or_none()
        if not wp and wp_title:
            wp = (
                await db.execute(select(WorkPackage).where(WorkPackage.title == wp_title))
            ).scalar_one_or_none()
        if not wp:
            ref = wp_rfo or wp_title or "(empty)"
            errors.append({"row": i, "reason": f"work_package '{ref}' not found"})
            continue

        # Duplicate check
        dup = (
            await db.execute(
                select(ShopStream).where(
                    ShopStream.work_package_id == wp.id,
                    ShopStream.shop_code == shop_code,
                )
            )
        ).scalar_one_or_none()
        if dup:
            skipped += 1
            continue

        obj = ShopStream(work_package_id=wp.id, shop_code=shop_code)
        db.add(obj)
        await db.flush()
        await write_audit(
            db, actor_id=actor_id,
            entity_type="shop_stream", entity_id=obj.id, action="CREATE",
            after={"work_package_id": obj.work_package_id, "shop_code": obj.shop_code},
        )
        created += 1

    return created, skipped, errors


def _parse_date(val: str | None):
    """Try to parse YYYY-MM-DD, return None on failure."""
    if not val or not val.strip():
        return None
    try:
        from datetime import date as _date
        return _date.fromisoformat(val.strip())
    except ValueError:
        return None
