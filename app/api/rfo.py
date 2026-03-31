"""RFO Metrics API (Section 8.7.2, 8.11)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.schemas.common import APIError
from app.services.rfo_service import build_rfo_analytics, can_view_rfo, get_work_package

router = APIRouter(prefix="/api/rfo", tags=["rfo"])


def _require_sup_plus(current_user: dict):
    if not can_view_rfo(current_user):
        raise APIError(403, "SUPERVISOR+ required", "FORBIDDEN")


async def _get_wp_or_404(db: AsyncSession, wp_id: int):
    wp = await get_work_package(db, wp_id)
    if not wp:
        raise APIError(404, "Work package not found", "NOT_FOUND")
    return wp


@router.get("/{work_package_id}/summary")
async def rfo_summary(
    work_package_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_sup_plus(current_user)
    wp = await _get_wp_or_404(db, work_package_id)
    analytics = await build_rfo_analytics(db, wp)
    return analytics["api_summary"]


@router.get("/{work_package_id}/metrics")
async def rfo_metrics(
    work_package_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_sup_plus(current_user)
    wp = await _get_wp_or_404(db, work_package_id)
    analytics = await build_rfo_analytics(db, wp)
    return analytics["api_metrics"]


@router.get("/{work_package_id}/blockers")
async def rfo_blockers(
    work_package_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_sup_plus(current_user)
    wp = await _get_wp_or_404(db, work_package_id)
    analytics = await build_rfo_analytics(db, wp)
    return analytics["api_blockers"]


@router.get("/{work_package_id}/worker-allocation")
async def rfo_worker_allocation(
    work_package_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_sup_plus(current_user)
    wp = await _get_wp_or_404(db, work_package_id)
    analytics = await build_rfo_analytics(db, wp)
    return analytics["api_workers"]


@router.get("/{work_package_id}/burndown")
async def rfo_burndown(
    work_package_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_sup_plus(current_user)
    wp = await _get_wp_or_404(db, work_package_id)
    analytics = await build_rfo_analytics(db, wp)
    return analytics["api_burndown"]
