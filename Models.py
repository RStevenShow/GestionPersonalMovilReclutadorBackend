from typing import Optional, List
from sqlmodel import SQLModel, Field, Column, Relationship
from sqlalchemy.dialects.postgresql import ARRAY, FLOAT
from datetime import datetime, date, time

# =====================================================
# 1. MODELOS DE USUARIO
# =====================================================

class UserBase(SQLModel):
    username: str = Field(index=True, unique=True)
    email: str = Field(unique=True)
    full_name: Optional[str] = None
    role: str = Field(default="reclutador")
    photo_url: Optional[str] = None
    push_subscription: Optional[str] = None

class UserCreate(UserBase):
    password: str

class UserRead(UserBase):
    id: int
    created_at: datetime

class User(UserBase, table=True):
    __tablename__ = "user"

    id: Optional[int] = Field(default=None, primary_key=True)
    hashed_password: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    job_offers: List["JobOffer"] = Relationship(back_populates="owner")


# =====================================================
# 2. MODELOS DE AUTENTICACIÓN
# =====================================================

class Token(SQLModel):
    access_token: str
    token_type: str

class TokenData(SQLModel):
    username: Optional[str] = None


# =====================================================
# 3. MODELOS DE VACANTES
# =====================================================

class JobOfferBase(SQLModel):
    title: str = Field(index=True)
    description_original: str
    salary_range: Optional[str] = None
    experience_years: Optional[int] = 0
    skills_required: Optional[str] = None
    responsibilities: Optional[str] = None
    location: Optional[str] = "Remoto"
    priority: str = Field(default="medium")

    max_candidatos: int = Field(default=1)
    estado: str = Field(default="abierta")

    owner_id: Optional[int] = Field(default=None, foreign_key="user.id")


class JobOfferCreate(JobOfferBase):
    pass


class CandidateReadMinimal(SQLModel):
    id: int
    name: str
    match_score: float
    rationale: Optional[str] = None
    file_path: Optional[str] = None


class JobOfferRead(JobOfferBase):
    id: int
    description_en: Optional[str] = None
    candidates: List[CandidateReadMinimal] = []


class JobOffer(JobOfferBase, table=True):
    __tablename__ = "job_offer"

    id: Optional[int] = Field(default=None, primary_key=True)
    description_en: Optional[str] = None
    vector: Optional[List[float]] = Field(sa_column=Column(ARRAY(FLOAT)))

    owner: Optional["User"] = Relationship(back_populates="job_offers")

    candidates: List["Candidate"] = Relationship(
        back_populates="job_offer",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )


# =====================================================
# 4. MODELOS DE CANDIDATOS
# =====================================================

class CandidateBase(SQLModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    file_path: Optional[str] = None


class CandidateRead(CandidateBase):
    id: int
    match_score: float
    rationale: Optional[str] = None
    text_extracted: Optional[str] = None


class Candidate(CandidateBase, table=True):
    __tablename__ = "candidate"

    id: Optional[int] = Field(default=None, primary_key=True)
    text_extracted: Optional[str] = None
    text_en: Optional[str] = None
    vector: Optional[List[float]] = Field(sa_column=Column(ARRAY(FLOAT)))

    match_score: float = 0.0
    rationale: Optional[str] = None

    job_offer_id: Optional[int] = Field(default=None, foreign_key="job_offer.id")
    job_offer: Optional["JobOffer"] = Relationship(back_populates="candidates")

    interviews: List["Interview"] = Relationship(back_populates="candidate")


# =====================================================
# 5. MODELOS DE ENTREVISTAS
# =====================================================

class InterviewBase(SQLModel):
    candidate_id: int = Field(foreign_key="candidate.id")
    fecha: date
    hora: time
    metodo: str

    completada: bool = Field(default=False)
    calificacion: Optional[float] = None
    contratado: bool = Field(default=False)


class Interview(InterviewBase, table=True):
    __tablename__ = "interview"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")

    candidate: Optional["Candidate"] = Relationship(back_populates="interviews")


class InterviewCreate(InterviewBase):
    pass


class InterviewRead(InterviewBase):
    id: int