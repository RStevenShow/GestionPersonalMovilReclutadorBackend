import os
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlmodel import Session, select
from typing import List
from datetime import timedelta
from jose import JWTError, jwt

# --- IMPORTS DE MODELOS Y UTILIDADES ---
from database import create_db_and_tables, get_session
from Models import (
    JobOffer, JobOfferCreate, JobOfferRead, 
    Candidate, CandidateRead, 
    User, UserCreate, UserRead, Token,Interview, InterviewCreate, InterviewRead
)
from auth_utils import (
    get_password_hash, verify_password, create_access_token, 
    SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES
)
from ai_service import (
    load_models, translate_text, get_embedding,
    extract_text_from_pdf, calculate_similarity,
    generate_rationale, extract_email_from_text,
    extract_phone_from_text
)

app = FastAPI(title="MarkNica Recruiting AI API")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuracion de directorio de archivos
UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

# Montaje de archivos estaticos con rutas relativas
app.mount("/static", StaticFiles(directory=UPLOAD_DIR), name="static")

@app.on_event("startup")
def on_startup():
    print("Sincronizando Base de Datos y Modelos de IA...")
    create_db_and_tables() 
    load_models()

# --- SEGURIDAD ---
def get_current_user(token: str = Depends(oauth2_scheme), session: Session = Depends(get_session)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sesion expirada o no valida",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    user = session.exec(select(User).where(User.username == username)).first()
    if user is None:
        raise credentials_exception
    return user

# --- ENDPOINTS DE AUTENTICACION ---

@app.post("/auth/register", response_model=UserRead)
def register(user: UserCreate, session: Session = Depends(get_session)):
    existing = session.exec(select(User).where(User.username == user.username)).first()
    if existing:
        raise HTTPException(status_code=400, detail="El correo ya esta registrado")
    
    db_user = User(
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        hashed_password=get_password_hash(user.password),
        role=user.role
    )
    session.add(db_user)
    session.commit()
    session.refresh(db_user)
    return db_user

@app.post("/auth/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.username == form_data.username)).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

# --- GESTION DE OFERTAS ---

@app.post("/offers/", response_model=JobOfferRead)
def create_offer(
    offer: JobOfferCreate, 
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    offer_data = offer.dict()
    offer_data.pop("owner_id", None) 

    full_context = f"Puesto: {offer.title}. Descripcion: {offer.description_original}."
    desc_en = translate_text(full_context)
    vector = get_embedding(desc_en)
    
    new_offer = JobOffer(
        **offer_data,
        description_en=desc_en,
        vector=vector,
        owner_id=current_user.id 
    )
    
    session.add(new_offer)
    session.commit()
    session.refresh(new_offer)
    return new_offer

@app.get("/offers/", response_model=List[JobOfferRead])
def read_offers(session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    return session.exec(select(JobOffer).where(JobOffer.owner_id == current_user.id)).all()

@app.delete("/offers/{offer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_offer(offer_id: int, session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    offer = session.get(JobOffer, offer_id)
    if not offer or offer.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No autorizado")
    
    session.delete(offer)
    session.commit()
    return None

# --- GESTION DE PERFIL ---

@app.get("/users/me", response_model=UserRead)
def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user

@app.post("/users/me/photo")
async def upload_profile_photo(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    extension = file.filename.split(".")[-1]
    nuevo_nombre = f"perfil_{current_user.id}.{extension}"
    ruta_final = os.path.join(UPLOAD_DIR, nuevo_nombre)

    content = await file.read()
    with open(ruta_final, "wb") as f:
        f.write(content)

    foto_url = f"/static/{nuevo_nombre}"
    current_user.photo_url = foto_url
    session.add(current_user)
    session.commit()
    
    return {"foto_url": foto_url}

@app.put("/users/me", response_model=UserRead)
def update_user_me(
    user_data: UserCreate, 
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    if user_data.full_name:
        current_user.full_name = user_data.full_name
    if user_data.email:
        current_user.email = user_data.email
    if user_data.password:
        current_user.hashed_password = get_password_hash(user_data.password)
    
    session.add(current_user)
    session.commit()
    session.refresh(current_user)
    return current_user

# --- PROCESAMIENTO DE CANDIDATOS ---

@app.post("/offers/{offer_id}/upload_cvs", response_model=List[CandidateRead])
async def upload_cvs(
    offer_id: int, 
    files: List[UploadFile] = File(...), 
    session: Session = Depends(get_session), 
    current_user: User = Depends(get_current_user)
):
    offer = session.get(JobOffer, offer_id)
    if not offer or offer.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Acceso denegado")
    
    results = []
    for file in files:
        file_location = os.path.join(UPLOAD_DIR, file.filename)
        content = await file.read()
        
        with open(file_location, "wb") as f:
            f.write(content)
            
        text_es = extract_text_from_pdf(content)
        email = extract_email_from_text(text_es)
        phone = extract_phone_from_text(text_es)
        text_en = translate_text(text_es)
        vec_cv = get_embedding(text_en)
        
        score = calculate_similarity(vec_cv, offer.vector)
        rationale = generate_rationale(text_en, offer.description_en)
        
        new_candidate = Candidate(
            name=file.filename,
            email=email,
            phone=phone,
            file_path=f"/static/{file.filename}",
            text_extracted=text_es,
            text_en=text_en,
            vector=vec_cv,
            match_score=score,
            rationale=rationale,
            job_offer_id=offer.id
        )
        
        session.add(new_candidate)
        results.append(new_candidate)
    
    session.commit()
    results.sort(key=lambda x: x.match_score, reverse=True)
    return results

# --- DASHBOARD Y ESTADISTICAS ---

@app.get("/api/dashboard-stats")
def get_dashboard_stats(session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    todos_los_candidatos = session.exec(select(Candidate)).all()
    todas_las_vacantes = session.exec(select(JobOffer)).all()
    
    num_candidatos = len(todos_los_candidatos)
    num_vacantes = len(todas_las_vacantes)
    
    promedio_match = 0
    if num_candidatos > 0:
        promedio_match = sum(c.match_score for c in todos_los_candidatos) / num_candidatos

    stats_proceso = {
        "screening": len([c for c in todos_los_candidatos if c.match_score < 40]),
        "entrevistas": len([c for c in todos_los_candidatos if 40 <= c.match_score < 75]),
        "oferta": len([c for c in todos_los_candidatos if c.match_score >= 75]),
        "contratados": 0
    }

    return {
        "appNombre": "MarkNica AI",
        "candidatos": num_candidatos,
        "entrevistas": stats_proceso["entrevistas"],
        "match": round(promedio_match, 1),
        "acciones": [
            f"Tienes {num_vacantes} vacantes activas",
            f"IA analizo {num_candidatos} perfiles"
        ],
        "proceso": stats_proceso
    }

@app.get("/offers/{offer_id}", response_model=JobOfferRead)
def read_single_offer(
    offer_id: int, 
    session: Session = Depends(get_session), 
    current_user: User = Depends(get_current_user)
):
    offer = session.get(JobOffer, offer_id)
    if not offer or offer.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="No encontrada")
    return offer



@app.get("/candidates/{candidate_id}", response_model=CandidateRead)
def read_candidate(
    candidate_id: int, 
    session: Session = Depends(get_session), 
    current_user: User = Depends(get_current_user)
):
    # Buscar al candidato en la BD
    candidate = session.get(Candidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidato no encontrado")
    
    # Validar que el candidato pertenezca a una vacante creada por este reclutador
    offer = session.get(JobOffer, candidate.job_offer_id)
    if not offer or offer.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Acceso denegado a este perfil")
        
    return candidate


@app.post("/interviews/", response_model=InterviewRead)
def create_interview(
    interview: InterviewCreate, 
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    # Validar que el candidato existe y le pertenece a este usuario
    candidate = session.get(Candidate, interview.candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="El candidato no existe")
        
    offer = session.get(JobOffer, candidate.job_offer_id)
    if not offer or offer.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No tienes permiso para agendar a este candidato")

    # Guardar la entrevista en la base de datos
    db_interview = Interview(**interview.dict(), user_id=current_user.id)
    session.add(db_interview)
    session.commit()
    session.refresh(db_interview)
    
    return db_interview