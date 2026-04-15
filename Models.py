from typing import Optional, List
from sqlmodel import SQLModel, Field, Column, Relationship
from sqlalchemy.dialects.postgresql import ARRAY, FLOAT
from datetime import datetime

# --- 1. MODELOS DE USUARIO ---
class UserBase(SQLModel):
    username: str = Field(index=True, unique=True)
    email: str = Field(unique=True)
    full_name: Optional[str] = None
    role: str = Field(default="reclutador")
    photo_url: Optional[str] = None # Agregado para la foto de perfil de Ramón

class UserCreate(UserBase):
    password: str

class UserRead(UserBase):
    id: int
    created_at: datetime
    photo_url: Optional[str] = None

class User(UserBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    hashed_password: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Relación: Un reclutador tiene muchas vacantes
    job_offers: List["JobOffer"] = Relationship(back_populates="owner")


# --- 2. MODELOS DE TOKEN (AUTH) ---
class Token(SQLModel):
    access_token: str
    token_type: str

class TokenData(SQLModel):
    username: Optional[str] = None


# --- 3. MODELOS DE OFERTA (VACANTES) ---
class JobOfferBase(SQLModel):
    title: str = Field(index=True)
    description_original: str
    salary_range: Optional[str] = None
    experience_years: Optional[int] = 0
    skills_required: Optional[str] = None
    responsibilities: Optional[str] = None
    location: Optional[str] = "Remoto"
    priority: str = Field(default="medium")
    
    # Clave foránea al usuario (Reclutador)
    owner_id: Optional[int] = Field(default=None, foreign_key="user.id")

class JobOfferCreate(JobOfferBase):
    pass

# DTO para evitar circularidad al leer candidatos dentro de una oferta
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
    id: Optional[int] = Field(default=None, primary_key=True)
    description_en: Optional[str] = None
    vector: Optional[List[float]] = Field(sa_column=Column(ARRAY(FLOAT)))
    
    # Relación hacia el dueño
    owner: Optional[User] = Relationship(back_populates="job_offers")
    
    # Relación hacia sus candidatos (se borran si se borra la oferta)
    candidates: List["Candidate"] = Relationship(
        back_populates="job_offer", 
        sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )


# --- 4. MODELOS DE CANDIDATO ---
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
    id: Optional[int] = Field(default=None, primary_key=True)
    text_extracted: Optional[str] = None
    text_en: Optional[str] = None
    vector: Optional[List[float]] = Field(sa_column=Column(ARRAY(FLOAT)))
    match_score: float = 0.0
    rationale: Optional[str] = None
    
    # Foreign Key explícita a la tabla joboffer
    job_offer_id: Optional[int] = Field(default=None, foreign_key="joboffer.id")
    job_offer: Optional[JobOffer] = Relationship(back_populates="candidates")