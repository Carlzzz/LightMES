from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.models import User


def session_user_id(request: Request) -> int | None:
    """Return the authenticated user_id from the session, or None if not logged in."""
    return request.session.get("user_id")


def require_login(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency: require an authenticated session, return the current User.

    Raises 401 if no valid session. Use on JSON API write endpoints.
    """
    user_id = session_user_id(request)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要登录")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要登录")
    return user
