"""
Authentication helpers that work for both primary and secondary backends.
Secondary backends validate tokens via primary backend API.
"""

import os
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from typing import Dict, Any
import asyncio

from .database import get_db
from .services.federation import get_federation_client

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def _decode_token_locally(token: str) -> Dict[str, Any]:
    """Decode JWT token locally (for primary backend)"""
    from jose import jwt
    from .routers.auth import JWT_SECRET, JWT_ALGORITHM
    
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return claims
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")


async def validate_token_federated(token: str) -> Dict[str, Any]:
    """
    Validate token via primary backend (for secondary backends).
    Returns user info: {username, role}
    """
    federation_client = get_federation_client()
    
    if not federation_client.is_enabled():
        # This is primary backend or federation not configured, use local validation
        return _decode_token_locally(token)
    
    # Secondary backend: validate via primary
    user_info = await federation_client.validate_token(token)
    
    if not user_info or not user_info.get("valid"):
        raise HTTPException(status_code=401, detail="Invalid token")
    
    return {
        "sub": user_info.get("username"),
        "role": user_info.get("role")
    }


def get_current_user(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
    """
    Get current user from token (works for both primary and secondary backends).
    For secondary backends, validates token via primary backend API.
    """
    backend_type = os.getenv("BACKEND_TYPE", "secondary")
    
    if backend_type == "primary":
        # Primary backend: decode locally
        return _decode_token_locally(token)
    else:
        # Secondary backend: validate via primary (async)
        # Note: FastAPI dependencies don't support async, so we use asyncio.run
        return asyncio.run(validate_token_federated(token))


def get_current_user_role(token: str = Depends(oauth2_scheme)) -> str:
    """Get current user's role"""
    user = get_current_user(token)
    return user.get("role", "")


def require_admin(token: str = Depends(oauth2_scheme)) -> str:
    """Dependency that requires admin role"""
    role = get_current_user_role(token)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return role


def require_editor(token: str = Depends(oauth2_scheme)) -> str:
    """Dependency that requires editor or admin role"""
    role = get_current_user_role(token)
    if role not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail="Editor or admin access required")
    return role

