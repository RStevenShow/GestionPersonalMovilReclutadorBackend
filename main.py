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
    User, UserCreate, UserRead, Token
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

UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

# Montamos la carpeta para que las fotos sean accesibles vía URL
app.mount("/static", StaticFiles(directory=UPLOAD_DIR), name="static")

@app.on_event("startup")
def on_startup():
    print(" Sincronizando Base de Datos y Modelos de IA...")
    create_db_and_tables() 
    load_models()

# --- SEGURIDAD ---
def get_current_user(token: str = Depends(oauth2_scheme), session: Session = Depends(get_session)):
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

# --- ENDPOINTS DE AUTENTICACIÓN ---

@app.post("/auth/register", response_model=UserRead)
def register(user: UserCreate, session: Session = Depends(get_session)):
    existing = session.exec(select(User).where(User.username == user.username)).first()
    if existing:
        raise HTTPException(status_code=400, detail="El correo ya está registrado")
    
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

# --- GESTIÓN DE OFERTAS (VACANTES) ---

@app.post("/offers/", response_model=JobOfferRead)
def create_offer(
    offer: JobOfferCreate, 
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    # Evitamos colisión de owner_id
    offer_data = offer.dict()
    offer_data.pop("owner_id", None) 

    full_context = f"Puesto: {offer.title}. Descripción: {offer.description_original}."
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
    # Ramón solo ve sus propias ofertas
    return session.exec(select(JobOffer).where(JobOffer.owner_id == current_user.id)).all()

@app.delete("/offers/{offer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_offer(offer_id: int, session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    offer = session.get(JobOffer, offer_id)
    if not offer or offer.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No tienes permiso para borrar esta vacante")
    
    session.delete(offer)
    session.commit()
    return None

# --- GESTIÓN DE PERFIL ---

@app.get("/users/me", response_model=UserRead)
def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user

@app.delete("/users/me")
def delete_my_account(session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    session.delete(current_user)
    session.commit()
    return {"detail": "Cuenta eliminada correctamente"}

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

    foto_url = f"http://127.0.0.1:8000/static/{nuevo_nombre}"
    
    # Actualizamos la foto en la base de datos para que persista
    current_user.photo_url = foto_url
    session.add(current_user)
    session.commit()
    
    return {"foto_url": foto_url}
# Actualización de datos de Usuario (nombre, correo o contraseña)

@app.put("/users/me", response_model=UserRead)
def update_user_me(
    user_data: UserCreate, # Usamos UserCreate para validar los campos que vienen
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """ Actualiza los datos de Ramón (Nombre o Contraseña) """
    
    #
    if user_data.full_name:
        current_user.full_name = user_data.full_name
    
    
    if user_data.email:
        current_user.email = user_data.email

    # Si Ramón cambió su contraseña, la hasheamos antes de guardar
    if user_data.password:
        current_user.hashed_password = get_password_hash(user_data.password)
    
    session.add(current_user)
    session.commit()
    session.refresh(current_user)
    return current_user
# --- PROCESAMIENTO DE CANDIDATOS (CVs) ---

@app.post("/offers/{offer_id}/upload_cvs", response_model=List[CandidateRead])
async def upload_cvs(
    offer_id: int, 
    files: List[UploadFile] = File(...), 
    session: Session = Depends(get_session), 
    current_user: User = Depends(get_current_user)
):
    offer = session.get(JobOffer, offer_id)
    if not offer or offer.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Acceso denegado a esta vacante")
    
    results = []
    for file in files:
        file_location = f"{UPLOAD_DIR}/{file.filename}"
        content = await file.read()
        
        with open(file_location, "wb") as f:
            f.write(content)
            
        # --- Pipeline de Inteligencia Artificial ---
        text_es = extract_text_from_pdf(content)
        email = extract_email_from_text(text_es)
        phone = extract_phone_from_text(text_es)
        text_en = translate_text(text_es)
        vec_cv = get_embedding(text_en)
        
        # Comparación vectorial
        score = calculate_similarity(vec_cv, offer.vector)
        rationale = generate_rationale(text_en, offer.description_en)
        
        new_candidate = Candidate(
            name=file.filename,
            email=email,
            phone=phone,
            file_path=f"http://127.0.0.1:8000/static/{file.filename}",
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
    # Los mejores candidatos primero
    results.sort(key=lambda x: x.match_score, reverse=True)
    return results

# esto es para el dashboard, estadísticas generales y gráficas de proceso
@app.get("/api/dashboard-stats")
def get_dashboard_stats(session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    # 1. Traer todos los datos de la base de datos
    todos_los_candidatos = session.exec(select(Candidate)).all()
    todas_las_vacantes = session.exec(select(JobOffer)).all()
    
    num_candidatos = len(todos_los_candidatos)
    num_vacantes = len(todas_las_vacantes)
    
    # 2. Calcular promedio de Match IA
    promedio_match = 0
    if num_candidatos > 0:
        promedio_match = sum(c.match_score for c in todos_los_candidatos) / num_candidatos

    # 3. Lógica para el gráfico de "Estado del proceso"
    # Clasificamos a los candidatos por su score de IA
    stats_proceso = {
        "screening": len([c for c in todos_los_candidatos if c.match_score < 40]),
        "entrevistas": len([c for c in todos_los_candidatos if 40 <= c.match_score < 75]),
        "oferta": len([c for c in todos_los_candidatos if c.match_score >= 75]),
        "contratados": 0  # Esto lo llenaremos cuando agreguemos el campo 'estado'
    }

    return {
        "appNombre": "MarkNica AI",
        "candidatos": num_candidatos,
        "entrevistas": stats_proceso["entrevistas"],
        "match": round(promedio_match, 1),
        "acciones": [
            f"Tienes {num_vacantes} vacantes activas hoy",
            f"IA analizó {num_candidatos} perfiles recientemente"
        ],
        "proceso": stats_proceso
    }

# Endpoint para obtener una vacante específica con sus candidatos vinculados (para el listado de candidatos en detalle vacantess    )
@app.get("/offers/{offer_id}", response_model=JobOfferRead)
def read_single_offer(
    offer_id: int, 
    session: Session = Depends(get_session), 
    current_user: User = Depends(get_current_user)
):
    """ Obtiene una vacante específica con sus candidatos vinculados """
    
    # Buscamos la oferta en la base de datos
    offer = session.get(JobOffer, offer_id)
    
    # Verificamos que exista y que pertenezca al usuario actual
    if not offer or offer.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="La vacante no existe o no tienes permiso")
    
    # Gracias a la relación 'relationship' en Models.py, 
    # offer.candidates ya contiene la lista de personas que aplicaron.
    
    return offer

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)