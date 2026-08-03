from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.models import User


def current_user_or_none(request: Request, db: Session) -> User | None:
    """Return the current User for a valid session, or None if not authenticated.

    Returns None when there is no session, the session user_id is not an int,
    the user no longer exists, or the user has been deactivated. Never raises.
    """
    user_id = request.session.get("user_id")
    if not isinstance(user_id, int):
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


def require_login(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency: require an authenticated session, return the current User.

    Raises 401 if no valid session. Use on JSON API write endpoints.
    """
    user = current_user_or_none(request, db)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要登录")
    return user
