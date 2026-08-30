"""Read-only fee-rule endpoints."""

from datetime import date

from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.cache.redis_client import get_cached_json, set_cached_json
from app.database import get_db
from app.models import Customer, FeeRule
from app.schemas.fee_rule import FeeRuleRead


router = APIRouter(prefix="/fee-rules", tags=["fee rules"])


@router.get("/{customer_id}", response_model=list[FeeRuleRead])
def list_active_fee_rules(
    customer_id: int,
    response: Response,
    use_cache: bool = True,
    session: Session = Depends(get_db),
) -> list[FeeRule] | list[dict[str, object]]:
    """Return the rule versions that are active today for the requested customer."""
    start_time = perf_counter()
    cache_key = f"customer:{customer_id}:fee-rules"
    cached = get_cached_json(cache_key) if use_cache else None
    cache_hit = cached is not None
    if cached is not None:
        result = cached
    else:
        customer = session.get(Customer, customer_id)
        if customer is None:
            raise HTTPException(status_code=404, detail="Customer not found.")
        today = date.today()
        # Customer-specific overrides (highest priority)
        overrides = list(
            session.scalars(
                select(FeeRule)
                .where(
                    FeeRule.customer_id == customer_id,
                    FeeRule.effective_from <= today,
                    or_(FeeRule.effective_to.is_(None), FeeRule.effective_to >= today),
                )
                .order_by(FeeRule.fee_type, FeeRule.effective_from.desc())
            ).all()
        )
        overridden_fee_types = {rule.fee_type for rule in overrides}

        # Fallback tier templates for fee types not overridden by customer contract
        templates = list(
            session.scalars(
                select(FeeRule)
                .where(
                    FeeRule.customer_id.is_(None),
                    FeeRule.account_tier == customer.account_tier,
                    FeeRule.effective_from <= today,
                    or_(FeeRule.effective_to.is_(None), FeeRule.effective_to >= today),
                )
                .order_by(FeeRule.fee_type, FeeRule.effective_from.desc())
            ).all()
        )
        active_rules = list(overrides)
        for template in templates:
            if template.fee_type not in overridden_fee_types:
                active_rules.append(template)
                overridden_fee_types.add(template.fee_type)

        result = [FeeRuleRead.model_validate(rule).model_dump(mode="json") for rule in active_rules]
        if use_cache:
            set_cached_json(cache_key, result, ttl_seconds=3_600)
    response.headers["X-Cache-Hit"] = str(cache_hit).lower()
    response.headers["X-Query-Time-Ms"] = f"{(perf_counter() - start_time) * 1_000:.3f}"
    return result
