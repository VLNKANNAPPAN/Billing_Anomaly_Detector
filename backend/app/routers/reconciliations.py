"""Read-only visibility into queued late-arrival reconciliation work."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import PendingReconciliation, ReconciliationRun
from app.schemas.adjustment import ReconciliationRunRead
from app.schemas.reconciliation import PendingReconciliationRead
from app.services.reconciliation import run_reconciliation


router = APIRouter(prefix="/reconciliations", tags=["reconciliations"])


@router.get("/pending", response_model=list[PendingReconciliationRead])
def list_pending_reconciliations(
    session: Session = Depends(get_db),
) -> list[PendingReconciliation]:
    """List unresolved periods in oldest-affected-first order."""
    return session.scalars(
        select(PendingReconciliation)
        .where(PendingReconciliation.status == "pending")
        .order_by(PendingReconciliation.earliest_affected_at)
    ).all()


@router.post(
    "/{marker_id}/run",
    response_model=ReconciliationRunRead,
    status_code=status.HTTP_201_CREATED,
)
def start_reconciliation_run(
    marker_id: int,
    session: Session = Depends(get_db),
) -> ReconciliationRun:
    """Recalculate one queued period and save reviewable adjustment candidates."""
    marker = session.get(PendingReconciliation, marker_id)
    if marker is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reconciliation not found")

    try:
        reconciliation_run = run_reconciliation(session, marker)
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return session.scalar(
        select(ReconciliationRun)
        .options(selectinload(ReconciliationRun.adjustment_candidates))
        .where(ReconciliationRun.id == reconciliation_run.id)
    )


@router.get("/runs/{run_id}", response_model=ReconciliationRunRead)
def get_reconciliation_run(
    run_id: int,
    session: Session = Depends(get_db),
) -> ReconciliationRun:
    """Retrieve a completed reconciliation run and its proposed adjustments."""
    reconciliation_run = session.scalar(
        select(ReconciliationRun)
        .options(selectinload(ReconciliationRun.adjustment_candidates))
        .where(ReconciliationRun.id == run_id)
    )
    if reconciliation_run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reconciliation run not found")
    return reconciliation_run
