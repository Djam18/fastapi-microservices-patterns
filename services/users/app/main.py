from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, mapped_column, Mapped
from sqlalchemy import select, String, Integer
import os, bcrypt, jwt, datetime, logging

# Users service — owns identity. Issues JWTs for the other services to verify.
# Key microservice principle: one service owns the data, others call it or verify tokens.
# Django: one AUTH_USER_MODEL per project. Here: one service per domain.

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./users.db")
JWT_SECRET   = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_ALGO     = "HS256"

engine       = create_async_engine(DATABASE_URL, echo=False)
AsyncSession_ = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class UserModel(Base):
    __tablename__ = "users"
    id:             Mapped[int]    = mapped_column(Integer, primary_key=True)
    email:          Mapped[str]    = mapped_column(String(255), unique=True)
    hashed_password: Mapped[str]  = mapped_column(String(255))
    role:           Mapped[str]    = mapped_column(String(50), default="user")


app = FastAPI(title="Users Service", version="0.1.0")


@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with AsyncSession_() as session:
        yield session


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    role: str = "user"


class UserResponse(BaseModel):
    id: int
    email: str
    role: str

    class Config:
        orm_mode = True


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _check(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def _issue_token(user: UserModel) -> str:
    payload = {
        "sub":   user.email,
        "uid":   user.id,
        "role":  user.role,
        "exp":   datetime.datetime.utcnow() + datetime.timedelta(hours=24),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


@app.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(body: UserCreate, db: AsyncSession = Depends(get_db)):
    existing = (await db.execute(select(UserModel).where(UserModel.email == body.email))).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    user = UserModel(email=body.email, hashed_password=_hash(body.password), role=body.role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@app.post("/token")
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(UserModel).where(UserModel.email == form.username))).scalar_one_or_none()
    if not user or not _check(form.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    return {"access_token": _issue_token(user), "token_type": "bearer"}


@app.get("/users/me", response_model=UserResponse)
async def me(token: str, db: AsyncSession = Depends(get_db)):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    user = (await db.execute(select(UserModel).where(UserModel.email == payload["sub"]))).scalar_one_or_none()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return user


@app.get("/health")
def health():
    return {"status": "ok", "service": "users"}
