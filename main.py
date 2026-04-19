import os
import time
from typing import List
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlmodel import Session, select
from jose import JWTError, jwt
from supabase import create_client, Client

# --- IMPORTS DE MODELOS Y UTILIDADES ---
from database import create_db_and_tables, get_session
from Models import (
    JobOffer, JobOfferCreate, JobOfferRead, 
    Candidate, CandidateRead, 
    User, UserCreate, UserRead, Token,
    Interview, InterviewCreate, InterviewRead
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

# --- CONFIGURACIÓN DE SUPABASE STORAGE ---
# Estas variables deben estar configuradas en el Dashboard de Render
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(title="MarkNica Recruiting AI API")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

# --- CONFIGURACIÓN DE CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    """Ejecutado al iniciar el servidor: Sincroniza tablas y carga modelos de IA."""
    create_db_and_tables() 
    load_models()

# --- UTILIDADES DE SEGURIDAD ---
def get_current_user(token: str = Depends(oauth2_scheme), session: Session = Depends(get_session)):
    """Valida el token JWT y retorna el usuario actual autenticado."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sesión expirada o no válida",
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

# =====================================================
#   ENDPOINTS DE AUTENTICACIÓN Y USUARIO
# =====================================================

@app.post("/auth/register", response_model=UserRead)
def register(user: UserCreate, session: Session = Depends(get_session)):
    """API: Registra un nuevo reclutador en la plataforma."""
    existing = session.exec(select(User).where(User.username == user.username)).first()
    if existing:
        raise HTTPException(status_code=400, detail="El correo ya está registrado")
    db_user = User(username=user.username, email=user.email, full_name=user.full_name,
                   hashed_password=get_password_hash(user.password), role=user.role)
    session.add(db_user)
    session.commit()
    session.refresh(db_user)
    return db_user

@app.post("/auth/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_session)):
    """API: Autentica al usuario y entrega un token de acceso JWT."""
    user = session.exec(select(User).where(User.username == form_data.username)).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/users/me", response_model=UserRead)
def read_users_me(current_user: User = Depends(get_current_user)):
    """API: Obtiene los datos del perfil del usuario logueado."""
    return current_user

@app.put("/users/me", response_model=UserRead)
def update_user_me(user_data: UserCreate, session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    """API: Actualiza nombre, correo o contraseña del perfil."""
    if user_data.full_name: current_user.full_name = user_data.full_name
    if user_data.email: current_user.email = user_data.email
    if user_data.password: current_user.hashed_password = get_password_hash(user_data.password)
    session.add(current_user)
    session.commit()
    session.refresh(current_user)
    return current_user

@app.post("/users/me/photo")
async def upload_profile_photo(file: UploadFile = File(...), session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    """API: Sube la foto de perfil al bucket de Supabase y actualiza la URL en la BD."""
    if not supabase: raise HTTPException(status_code=500, detail="Supabase no configurado")
    content = await file.read()
    file_path = f"profiles/user_{current_user.id}_{int(time.time())}.png"
    try:
        supabase.storage.from_("cvs").upload(path=file_path, file=content, file_options={"content-type": "image/png"})
        public_url = supabase.storage.from_("cvs").get_public_url(file_path)
        current_user.photo_url = public_url
        session.add(current_user)
        session.commit()
        return {"foto_url": public_url}
    except Exception: raise HTTPException(status_code=500, detail="Error al subir foto")

@app.delete("/users/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_me(session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    """API: Elimina la cuenta del usuario y todos sus datos relacionados (Casada manual)."""
    ofertas = session.exec(select(JobOffer).where(JobOffer.owner_id == current_user.id)).all()
    for oferta in ofertas:
        candidatos = session.exec(select(Candidate).where(Candidate.job_offer_id == oferta.id)).all()
        for cand in candidatos:
            entrevistas = session.exec(select(Interview).where(Interview.candidate_id == cand.id)).all()
            for ent in entrevistas: session.delete(ent)
        session.delete(oferta)
    session.delete(current_user)
    session.commit()
    return None

# =====================================================
#   ENDPOINTS DE GESTIÓN DE OFERTAS (VACANTES)
# =====================================================

@app.post("/offers/", response_model=JobOfferRead)
def create_offer(offer: JobOfferCreate, session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    """API: Crea una nueva vacante, traduce la descripción y genera su vector IA."""
    offer_data = offer.dict()
    offer_data.pop("owner_id", None) 
    full_context = f"Puesto: {offer.title}. Descripcion: {offer.description_original}."
    desc_en = translate_text(full_context)
    vector = get_embedding(desc_en)
    new_offer = JobOffer(**offer_data, description_en=desc_en, vector=vector, owner_id=current_user.id)
    session.add(new_offer)
    session.commit()
    session.refresh(new_offer)
    return new_offer

@app.get("/offers/", response_model=List[JobOfferRead])
def read_offers(session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    """API: Lista todas las vacantes creadas por el reclutador actual."""
    return session.exec(select(JobOffer).where(JobOffer.owner_id == current_user.id)).all()

@app.get("/offers/{offer_id}", response_model=JobOfferRead)
def read_single_offer(offer_id: int, session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    """API: Obtiene los detalles de una vacante específica junto con sus candidatos."""
    offer = session.get(JobOffer, offer_id)
    if not offer or offer.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="No encontrada")
    return offer

@app.delete("/offers/{offer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_offer(offer_id: int, session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    """API: Elimina una vacante y limpia sus candidatos y entrevistas asociadas."""
    offer = session.get(JobOffer, offer_id)
    if not offer or offer.owner_id != current_user.id: raise HTTPException(status_code=403, detail="No autorizado")
    candidatos = session.exec(select(Candidate).where(Candidate.job_offer_id == offer_id)).all()
    for cand in candidatos:
        entrevistas = session.exec(select(Interview).where(Interview.candidate_id == cand.id)).all()
        for ent in entrevistas: session.delete(ent)
    session.delete(offer)
    session.commit()
    return None

# =====================================================
#   ENDPOINTS DE CANDIDATOS Y PROCESAMIENTO IA
# =====================================================
@app.post("/offers/{offer_id}/upload_cvs", response_model=List[CandidateRead])
async def upload_cvs(
    offer_id: int, 
    files: List[UploadFile] = File(...), 
    session: Session = Depends(get_session), 
    current_user: User = Depends(get_current_user)
):
    """API: Sube CVs a Supabase, extrae datos con IA, calcula el Match y guarda resultados."""
    if not supabase: 
        raise HTTPException(status_code=500, detail="Configuración de Supabase ausente")
    
    offer = session.get(JobOffer, offer_id)
    if not offer or offer.owner_id != current_user.id: 
        raise HTTPException(status_code=403, detail="Acceso denegado")
    
    results = []
    for file in files:
        content = await file.read()
        # Nombre único para evitar que un archivo borre a otro
        timestamp = int(time.time())
        safe_name = f"{current_user.id}/{int(time.time())}_{file.filename.replace(' ', '_')}"
        
        try:
            # 1. Intentar la subida al bucket 'cvs'
            # Es vital que el bucket sea PUBLICO en el panel de Supabase
            response = supabase.storage.from_("cvs").upload(
                path=safe_name, 
                file=content, 
                file_options={"content-type": "application/pdf"}
            )
            
            # 2. Si la subida no dio error, generamos la URL pública
            # El método .get_public_url() NO valida si el archivo existe, por eso hay que subirlo bien antes
            public_url_obj = supabase.storage.from_("cvs").get_public_url(safe_name)
            
            # En algunas versiones del SDK esto es un objeto o un string. 
            # Si lo que guardas empieza por "https", está correcto.
            public_url = str(public_url_obj)

        except Exception as e:
            # Esto imprimirá el error real en los logs de Render (ej. "Bucket not found" o "New rows violated row-level security")
            print(f"Error subiendo a Supabase: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Fallo al subir a la nube: {str(e)}")
            
        # --- PROCESAMIENTO IA (Usando el contenido en memoria) ---
        text_es = extract_text_from_pdf(content)
        email = extract_email_from_text(text_es)
        phone = extract_phone_from_text(text_es)
        text_en = translate_text(text_es)
        vec_cv = get_embedding(text_en)
        
        score = calculate_similarity(vec_cv, offer.vector)
        rationale = generate_rationale(text_en, offer.description_en)
        
        # --- GUARDAR EN BASE DE DATOS ---
        # Usamos la public_url de Supabase,
        new_cand = Candidate(
            name=file.filename, 
            email=email, 
            phone=phone, 
            file_path=public_url, # <--- URL de la nube
            text_extracted=text_es, 
            text_en=text_en, 
            vector=vec_cv, 
            match_score=score, 
            rationale=rationale, 
            job_offer_id=offer.id
        )
        session.add(new_cand)
        results.append(new_cand)
    
    session.commit()
    results.sort(key=lambda x: x.match_score, reverse=True)
    return results

@app.get("/candidates/{candidate_id}", response_model=CandidateRead)
def read_candidate(candidate_id: int, session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    """API: Obtiene el análisis detallado de la IA para un candidato específico."""
    candidate = session.get(Candidate, candidate_id)
    if not candidate: raise HTTPException(status_code=404, detail="No encontrado")
    offer = session.get(JobOffer, candidate.job_offer_id)
    if not offer or offer.owner_id != current_user.id: raise HTTPException(status_code=403, detail="Acceso denegado")
    return candidate

# =====================================================
#   ENDPOINTS DE AGENDA (ENTREVISTAS)
# =====================================================

@app.post("/interviews/", response_model=InterviewRead)
def create_interview(interview: InterviewCreate, session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    """API: Programa una nueva entrevista para un candidato seleccionado."""
    cand = session.get(Candidate, interview.candidate_id)
    if not cand: raise HTTPException(status_code=404, detail="Candidato no existe")
    db_interview = Interview(**interview.dict(), user_id=current_user.id)
    session.add(db_interview); session.commit(); session.refresh(db_interview)
    return db_interview

@app.get("/interviews/", response_model=list)
def read_interviews(session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    """API: Obtiene la lista de entrevistas cruzando datos de candidato y vacante para la Agenda."""
    interviews = session.exec(select(Interview).where(Interview.user_id == current_user.id)).all()
    result = []
    for iv in interviews:
        cand = session.get(Candidate, iv.candidate_id)
        if cand:
            offer = session.get(JobOffer, cand.job_offer_id)
            result.append({
                "id": iv.id, "candidate_id": cand.id, "nombre": cand.name.replace(".pdf", ""),
                "puesto": offer.title if offer else "Vacante eliminada",
                "fecha": iv.fecha, "hora": iv.hora, "metodo": iv.metodo, "match": cand.match_score
            })
    return result

# =====================================================
#   ENDPOINTS DE DASHBOARD Y ESTADÍSTICAS
# =====================================================

@app.get("/api/dashboard-stats")
def get_dashboard_stats(session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    """API: Genera las estadísticas globales de candidatos, matches y procesos para el Inicio."""
    candidatos = session.exec(select(Candidate).join(JobOffer).where(JobOffer.owner_id == current_user.id)).all()
    vacantes = session.exec(select(JobOffer).where(JobOffer.owner_id == current_user.id)).all()
    num_cand = len(candidatos); num_vac = len(vacantes)
    promedio = sum(c.match_score for c in candidatos) / num_cand if num_cand > 0 else 0
    
    proceso = {
        "screening": len([c for c in candidatos if c.match_score < 40]),
        "entrevistas": len(session.exec(select(Interview).where(Interview.user_id == current_user.id)).all()),
        "oferta": len([c for c in candidatos if c.match_score >= 75]), "contratados": 0
    }
    return {
        "appNombre": "MarkNica AI", "candidatos": num_cand, "match": round(promedio, 1),
        "acciones": [f"Tienes {num_vac} vacantes activas", f"IA analizó {num_cand} perfiles"],
        "proceso": proceso
    }