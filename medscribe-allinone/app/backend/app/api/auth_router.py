"""
Auth API 路由：註冊、登錄、獲取當前用戶信息
"""

import re

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field

from app import db
from app.auth import (
    create_token,
    get_current_user,
    hash_password,
    verify_password,
)


router = APIRouter(prefix="/auth", tags=["認證"])


# ============ 請求/響應模型 ============
class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6, max_length=64)
    display_name: str = Field("", max_length=64)
    role: str = Field("doctor", pattern=r"^(doctor|nurse|admin)$")


class LoginRequest(BaseModel):
    username: str
    password: str


class UserInfo(BaseModel):
    id: int
    username: str
    display_name: str
    role: str


class LoginResponse(BaseModel):
    token: str
    user: UserInfo


def _validate_username(username: str) -> None:
    """用戶名只能包含字母、數字、底線、點、連字符"""
    if not re.match(r"^[a-zA-Z0-9_.\-]+$", username):
        raise HTTPException(400, "用戶名只能包含字母、數字、_ . -")


# ============ 路由 ============
@router.post("/register", response_model=UserInfo, summary="註冊新用戶")
def register(req: RegisterRequest):
    """
    註冊新賬號。

    - 同一台機器多個醫生可以各自註冊，記錄分賬
    - 用戶名全局唯一
    """
    _validate_username(req.username)
    if db.get_user_by_username(req.username):
        raise HTTPException(status_code=400, detail=f"用戶名 '{req.username}' 已被使用")
    user_id = db.create_user(
        username=req.username,
        display_name=req.display_name or req.username,
        password_hash=hash_password(req.password),
        role=req.role,
    )
    logger.info(f"[Auth] 新用戶註冊：{req.username} ({req.role}) id={user_id}")
    return UserInfo(
        id=user_id,
        username=req.username,
        display_name=req.display_name or req.username,
        role=req.role,
    )


@router.post("/login", response_model=LoginResponse, summary="登錄")
def login(req: LoginRequest):
    """用戶名 + 密碼登錄，返回 JWT token（24 小時有效）"""
    user = db.get_user_by_username(req.username)
    if not user or not verify_password(req.password, user["password_hash"]):
        logger.warning(f"[Auth] 登錄失敗：{req.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用戶名或密碼錯誤",
        )
    db.update_last_login(user["id"])
    token = create_token(user["id"], user["username"])
    logger.success(f"[Auth] 登錄成功：{req.username} id={user['id']}")
    return LoginResponse(
        token=token,
        user=UserInfo(
            id=user["id"],
            username=user["username"],
            display_name=user["display_name"],
            role=user["role"],
        ),
    )


@router.get("/me", response_model=UserInfo, summary="當前用戶信息")
def me(user=Depends(get_current_user)):
    """從 token 解析當前用戶信息"""
    return UserInfo(
        id=user["id"],
        username=user["username"],
        display_name=user["display_name"],
        role=user["role"],
    )


@router.get("/logs", summary="我的轉寫記錄（最近 50 條）")
def my_logs(user=Depends(get_current_user)):
    """個人中心 - 轉寫歷史"""
    rows = db.get_user_asr_logs(user["id"], limit=50)
    return {"user_id": user["id"], "logs": rows}
