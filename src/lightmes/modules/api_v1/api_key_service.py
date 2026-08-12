import secrets
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.auth.models import ApiKey, User
from lightmes.shared.security import hash_password, verify_password

API_KEY_PREFIX_LIVE = "lmk_live_"
API_KEY_PREFIX_TEST = "lmk_test_"
API_KEY_RANDOM_LEN = 32
API_KEY_PREFIX_DISPLAY_LEN = 12


class ApiKeyService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        name: str,
        user_id: int,
        scopes: list[str],
        expires_at: datetime | None = None,
        test: bool = False,
    ) -> tuple[str, ApiKey]:
        prefix = API_KEY_PREFIX_TEST if test else API_KEY_PREFIX_LIVE
        random_part = secrets.token_urlsafe(API_KEY_RANDOM_LEN)[:API_KEY_RANDOM_LEN]
        full_key = prefix + random_part
        key_prefix = full_key[:API_KEY_PREFIX_DISPLAY_LEN]
        key_hash = hash_password(full_key)
        record = ApiKey(
            name=name, key_prefix=key_prefix, key_hash=key_hash,
            user_id=user_id, scopes=scopes,
            is_active=True, expires_at=expires_at,
        )
        self.db.add(record); self.db.flush()
        return full_key, record

    def validate(self, full_key: str) -> tuple[User, ApiKey]:
        """Validate a full API key. Raises HTTPException(401) on any failure."""
        if not (full_key.startswith(API_KEY_PREFIX_LIVE) or full_key.startswith(API_KEY_PREFIX_TEST)):
            raise HTTPException(status_code=401, detail="无效的 API Key 格式")
        prefix = full_key[:API_KEY_PREFIX_DISPLAY_LEN]
        # Argon2 不能直接查 —— 用前缀筛候选，再逐个 verify
        candidates = list(self.db.execute(
            select(ApiKey).where(ApiKey.key_prefix == prefix, ApiKey.is_active.is_(True))
        ).scalars().all())
        for k in candidates:
            if verify_password(full_key, k.key_hash):
                if not self._is_valid(k):
                    raise HTTPException(status_code=401, detail="API Key 已过期或被吊销")
                user = self.db.get(User, k.user_id)
                if user is None or not user.is_active:
                    raise HTTPException(status_code=401, detail="API Key 关联用户已停用")
                return user, k
        raise HTTPException(status_code=401, detail="API Key 无效或已吊销")

    def revoke(self, api_key_id: int, revoked_by_user_id: int) -> ApiKey:
        k = self.db.get(ApiKey, api_key_id)
        if k is None:
            from lightmes.shared.errors import NotFoundError
            raise NotFoundError(f"API Key 不存在: {api_key_id}")
        k.is_active = False
        k.revoked_at = datetime.now()
        k.revoked_by = revoked_by_user_id
        self.db.flush()
        return k

    def list_for_user(self, user_id: int) -> list[ApiKey]:
        return list(self.db.execute(
            select(ApiKey).where(ApiKey.user_id == user_id).order_by(ApiKey.id.desc())
        ).scalars().all())

    @staticmethod
    def _is_valid(k: ApiKey) -> bool:
        if not k.is_active:
            return False
        if k.revoked_at is not None:
            return False
        if k.expires_at is not None and k.expires_at < datetime.now():
            return False
        return True
