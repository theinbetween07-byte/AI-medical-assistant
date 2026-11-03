"""
Auth 工具：bcrypt 密碼哈希 + JWT 簽發/驗證 + FastAPI 依賴注入
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException, status

from app.config import JWT_ALGORITHM, JWT_EXPIRE_HOURS, JWT_SECRET
from app.db import get_user_by_id, get_user_by_username


# ============ 密碼 ============
def hash_password(plain: str) -> str:
    """bcrypt 哈希（自動加鹽）"""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校驗密碼"""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ============ JWT ============
def create_token(user_id: int, username: str) -> str:
    """簽發 JWT（默認 24 小時過期）"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=JWT_EXPIRE_HOURS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """解析並驗證 JWT"""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登錄已過期，請重新登錄",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="無效的登錄憑證",
        )


# ============ FastAPI 依賴 ============
async def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """
    從 Authorization: Bearer <token> 頭解析當前用戶

    用法：
        @router.post("/some-endpoint")
        def endpoint(user = Depends(get_current_user)):
            print(user["username"])
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登錄：請先登錄後再使用",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="認證格式錯誤，應為 'Bearer <token>'",
        )
    token = authorization[7:].strip()
    payload = decode_token(token)
    user = get_user_by_id(int(payload["sub"]))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用戶不存在或已被刪除",
        )
    return user


async def get_current_user_optional(
    authorization: Optional[str] = Header(None),
) -> Optional[dict]:
    """可選登錄：未登錄返回 None，登錄失敗返回 401"""
    if not authorization:
        return None
    return await get_current_user(authorization)
