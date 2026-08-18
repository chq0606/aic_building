"""
认证模块的请求/响应模型。

字段命名走 RESTful 习惯(snake_case),前端 axios 直接用。
密码强度校验放在 RegisterRequest 和 ChangePasswordRequest 里,
用 pydantic v2 的 field_validator,前端拿到的 422 错误信息天然带字段名。
"""
import re

from pydantic import BaseModel, EmailStr, Field, field_validator


PASSWORD_MIN_LEN = 8
# 至少 1 个字母 + 1 个数字,长度 >= 8。不强制特殊字符,避免用户记不住
PASSWORD_PATTERN = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).{8,}$")

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,32}$")


def _validate_password(v: str) -> str:
    if not PASSWORD_PATTERN.match(v):
        raise ValueError(
            "密码至少 8 位,且必须同时包含字母和数字"
        )
    return v


def _validate_username(v: str) -> str:
    if not USERNAME_PATTERN.match(v):
        raise ValueError(
            "用户名 3-32 位,仅支持字母、数字、下划线"
        )
    # demo 是保留账号,注册时禁止占用
    if v.lower() == "demo":
        raise ValueError("该用户名为系统保留,请换一个")
    return v


class RegisterRequest(BaseModel):
    username: str = Field(..., examples=["alice"])
    password: str = Field(..., examples=["alice12345"])
    email: EmailStr | None = None

    @field_validator("username")
    @classmethod
    def _check_username(cls, v: str) -> str:
        return _validate_username(v)

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return _validate_password(v)


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _check_new(cls, v: str) -> str:
        return _validate_password(v)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int  # access 过期秒数,前端用来定时刷新
    user: "UserOut"


class UserOut(BaseModel):
    id: str
    tenant_id: str
    username: str
    display_name: str | None = None
    email: str | None = None
    is_demo: bool = False
    tenant_code: str
    tenant_name: str


class UserInfoResponse(BaseModel):
    """给 /me 用:用户信息 + 租户信息 + 一点统计(楼数/读数)"""
    user: UserOut
    stats: dict


TokenResponse.model_rebuild()
