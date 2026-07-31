from sqlalchemy.orm import Session
from lightmes.modules.auth.models import User
from lightmes.modules.auth.repository import UserRepository
from lightmes.modules.auth.schemas import UserCreate
from lightmes.shared.security import hash_password, verify_password


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = UserRepository(db)

    def create_user(self, data: UserCreate) -> User:
        user = User(
            username=data.username,
            password_hash=hash_password(data.password),
            display_name=data.display_name,
            role=data.role,
        )
        return self.repo.add(user)

    def authenticate(self, username: str, password: str) -> User | None:
        user = self.repo.get_by_username(username)
        if user is None or not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user
