import os
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ConfigKV
from ..services.config_cache import get_config_cache

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@router.get("/api/settings/security-thresholds")
def get_security_thresholds(db: Session = Depends(get_db)) -> dict:
    """
    Return security status color thresholds.
    
    For primary backend: Reads from database, falls back to environment variables.
    For secondary backend: Reads from federation config cache (synced from primary).
    
    Logic:
    - RED: Risk Factor > redRiskFactor OR Critical > redCritical
    - ORANGE: Risk Factor between orangeMin and orangeMax AND Critical <= orangeCritical
    - GREEN: Risk Factor < greenRiskFactor AND Critical <= greenCritical
    """
    config_cache = get_config_cache()
    
    # Secondary backends: Use cached config from primary
    if not config_cache.is_primary:
        cached_thresholds = config_cache.get_security_thresholds()
        if cached_thresholds:
            return cached_thresholds
        # Fall through to defaults if cache is empty
    
    # Primary backend or cache miss: Read from database/env
    def getv(key: str, env_key: str, default: str) -> int:
        # Database takes priority over environment variable
        row = db.query(ConfigKV).filter(ConfigKV.key == key).one_or_none()
        if row and row.value:
            return int(row.value)
        return int(os.getenv(env_key, default))
    
    return {
        "red": {
            "riskFactor": getv("security_red_risk_factor", "SECURITY_RED_RISK_FACTOR", "10"),
            "critical": getv("security_red_critical", "SECURITY_RED_CRITICAL", "0"),
        },
        "orange": {
            "riskFactorMin": getv("security_orange_risk_factor_min", "SECURITY_ORANGE_RISK_FACTOR_MIN", "5"),
            "riskFactorMax": getv("security_orange_risk_factor_max", "SECURITY_ORANGE_RISK_FACTOR_MAX", "10"),
            "critical": getv("security_orange_critical", "SECURITY_ORANGE_CRITICAL", "0"),
        },
        "green": {
            "riskFactor": getv("security_green_risk_factor", "SECURITY_GREEN_RISK_FACTOR", "5"),
            "critical": getv("security_green_critical", "SECURITY_GREEN_CRITICAL", "0"),
        },
    }
