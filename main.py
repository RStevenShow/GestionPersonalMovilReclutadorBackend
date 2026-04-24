import os
import time
from typing import List
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlmodel import Session, select, create_engine
from sqlalchemy import func
from jose import JWTError, jwt
from supabase import create_client, Client
import json
from pywebpush import webpush, WebPushException
from fastapi import APIRouter, Depends, HTTPException, status

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

from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from pytz import timezone
    

from ai_service import (
    load_models, translate_text, get_embedding,
    extract_text_from_pdf, calculate_similarity,
    generate_rationale, extract_email_from_text,
    extract_phone_from_text
)

from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from pytz import timezone
from sqlmodel import select, Session

# Configuración de zona horaria para Nicaragua
nicaragua_tz = timezone('America/Managua')

# --- CONFIGURACIÓN DE BASE DE DATOS ---
# Importante: DATABASE_URL es diferente a SUPABASE_URL
DATABASE_URL = os.environ.get("DATABASE_URL") 

# Crear el engine de forma global para que el Scheduler lo vea
engine = create_engine(DATABASE_URL)


# --- CONFIGURACIÓN DE SUPABASE STORAGE ---
# Estas variables deben estar configuradas en el Dashboard de Render
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")


supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(title="MarkNica Recruiting AI API")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")
# --- CONFIGURACIÓN DE variables para notificacion ---
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY")
VAPID_EMAIL = os.environ.get("VAPID_EMAIL", "mailto:marknicaappmovilreclutador@gmail.com")

# Esta variable es necesaria para la funcion de envio
VAPID_CLAIMS = {
    "sub": VAPID_EMAIL
}
# Vlogs
if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
    print("WARNING: Las llaves VAPID no estan configuradas. Las notificaciones fallaran.")
# --- CONFIGURACIÓN DE CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
    """Sube CVs, procesa con IA, calcula match y guarda resultados."""

    # --- VALIDACIONES INICIALES ---
    offer = session.get(JobOffer, offer_id)
    if not offer or offer.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Acceso denegado")

    # --- VALIDAR SI LA VACANTE YA ESTA CERRADA ---
    if offer.estado == "cerrada":
        raise HTTPException(
            status_code=400,
            detail="La vacante ya está llena y no acepta más candidatos"
        )

    # --- VALIDAR SI LA VACANTE YA ESTA LLENA ---
    candidatos_actuales = session.exec(
        select(Candidate).where(Candidate.job_offer_id == offer.id)
    ).all()

    espacios_disponibles = offer.max_candidatos - len(candidatos_actuales)

    if espacios_disponibles <= 0:
        offer.estado = "cerrada"
        session.add(offer)
        session.commit()

        raise HTTPException(
            status_code=400,
            detail="La vacante ya alcanzó el límite de candidatos"
        )

    results = []

    # --- PROCESAMIENTO DE ARCHIVOS ---
    for file in files:
        content = await file.read()
        safe_name = f"{current_user.id}/{int(time.time())}_{file.filename.replace(' ', '_')}"

        # --- SUBIDA A SUPABASE ---
        try:
            supabase.storage.from_("cvs").upload(
                path=safe_name,
                file=content,
                file_options={"content-type": "application/pdf"}
            )

            public_url = str(
                supabase.storage.from_("cvs").get_public_url(safe_name)
            )

        except Exception as e:
            print(f"Error subiendo a Supabase: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Fallo al subir a la nube: {str(e)}"
            )

        # --- PROCESAMIENTO IA ---
        text_es = extract_text_from_pdf(content)
        email = extract_email_from_text(text_es)
        phone = extract_phone_from_text(text_es)
        text_en = translate_text(text_es)
        vec_cv = get_embedding(text_en)

        score = calculate_similarity(vec_cv, offer.vector)
        rationale = generate_rationale(text_en, offer.description_en)

        # --- CREAR CANDIDATO ---
        new_cand = Candidate(
            name=file.filename,
            email=email,
            phone=phone,
            file_path=public_url,
            text_extracted=text_es,
            text_en=text_en,
            vector=vec_cv,
            match_score=score,
            rationale=rationale,
            job_offer_id=offer.id
        )

        session.add(new_cand)
        results.append(new_cand)

    # --- GUARDAR EN DB ---
    session.commit()

    # --- ORDENAR RESULTADOS ---
    results.sort(key=lambda x: x.match_score, reverse=True)

    # --- ACTUALIZAR ESTADO DE LA VACANTE ---
    total_candidatos = session.exec(
        select(Candidate).where(Candidate.job_offer_id == offer.id)
    ).all()

    if len(total_candidatos) >= offer.max_candidatos:
        offer.estado = "cerrada"
        session.add(offer)
        session.commit()

    # --- NOTIFICACIÓN PUSH ---
    if current_user.push_subscription:
        mejor_match = results[0] if results else None

        mensaje_notif = f"Se han analizado {len(results)} nuevos perfiles."
        if mejor_match:
            mensaje_notif += f" El mejor tiene {round(mejor_match.match_score)}% de match."

        enviar_notificacion_push(
            subscription_str=current_user.push_subscription,
            titulo="¡Nuevos Candidatos!",
            mensaje=mensaje_notif,
            url_destino="/vacantes.html"
        )

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
                "fecha": iv.fecha, "hora": iv.hora, "metodo": iv.metodo, "match": cand.match_score,
                 "completada": iv.completada,
                 "calificacion": iv.calificacion
            })
    return result

# =====================================================
#   ENDPOINTS DE DASHBOARD Y ESTADÍSTICAS
# =====================================================

@app.get("/api/dashboard-stats")
def get_dashboard_stats(session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    
    # --- BASE ---
    candidatos = session.exec(
        select(Candidate).join(JobOffer).where(JobOffer.owner_id == current_user.id)
    ).all()

    entrevistas = session.exec(
        select(Interview).where(Interview.user_id == current_user.id)
    ).all()

    num_cand = len(candidatos)
    num_vac = len(session.exec(select(JobOffer).where(JobOffer.owner_id == current_user.id)).all())

    # --- PROCESO REAL ---
    
    # 1. Screening = candidatos SIN entrevista
    candidatos_con_entrevista = {e.candidate_id for e in entrevistas}
    screening = len([c for c in candidatos if c.id not in candidatos_con_entrevista])

    # 2. Entrevistas creadas
    total_entrevistas = len(entrevistas)

    # 3. Evaluados = entrevistas completadas
    evaluados = len([e for e in entrevistas if e.completada])

    # 4. Top candidatos (ej: calificación >= 80)
    top = len([e for e in entrevistas if e.completada and (e.calificacion or 0) >= 80])

    # --- MATCH PROMEDIO ---
    promedio_general = (
        sum(c.match_score for c in candidatos) / num_cand
        if num_cand > 0 else 0
    )

    return {
        "appNombre": "MarkNica AI",
        "candidatos": num_cand,
        "match": round(promedio_general, 1),

        "acciones": [
            f"Tienes {num_vac} vacantes activas",
            f"{total_entrevistas} entrevistas programadas"
        ],

        "proceso": {
            "screening": screening,
            "entrevistas": total_entrevistas,
            "oferta": evaluados,   
            "contratados": top    
        }
    }


@app.get("/api/dashboard-vacantes-ranking")
def get_vacantes_ranking(session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    """API: Entrega el Top 3 de candidatos por vacante, permitiendo alternar entre Match IA y Notas."""
    
    # 1. Obtener las ofertas de trabajo del reclutador actual
    ofertas = session.exec(select(JobOffer).where(JobOffer.owner_id == current_user.id)).all()
    
    resultado = []
    
    for oferta in ofertas:
        # 2. Obtener candidatos asociados a esta oferta
        # (Asegúrate de que Candidate tenga la relación con JobOffer)
        candidatos_data = []
        
        for cand in oferta.candidates:
            # 3. Buscar si tiene una entrevista completada para obtener la calificación real
            # Esto une tu criterio de "Calificación de Entrevista" con el "Match IA"
            entrevista = session.exec(
                select(Interview).where(
                    Interview.candidate_id == cand.id,
                    Interview.completada == True
                )
            ).first()
            
            candidatos_data.append({
                "id": cand.id,
                "nombre": cand.name,
                "email": cand.email,
                "match_score": round(cand.match_score, 1),
                "calificacion_entrevista": entrevista.calificacion if entrevista else 0
            })

        # Agregamos la vacante solo si tiene candidatos
        if candidatos_data:
            resultado.append({
                "id": str(oferta.id),
                "titulo": oferta.title,
                "candidatos": candidatos_data
            })
    
    # Devolvemos la estructura exacta que el Frontend espera con 'vacantes'
    return {"vacantes": resultado}



#=====================================================
#  ENDPOINTS DE NOTIFICACIONES PUSH
 #=====================================================

import json 
#esta guarda la suscripcion del usuario para poder enviarle notificaciones push en el futuro
@app.post("/api/save-subscription")
def save_subscription(
    subscription: dict,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    try:
        current_user.push_subscription = json.dumps(subscription)
        session.add(current_user)
        session.commit()

        print(f"INFO: Suscripción guardada para user {current_user.id}")
        return {"ok": True}

    except Exception as e:
        print(f"ERROR save_subscription: {e}")
        raise HTTPException(status_code=500, detail="Error guardando suscripción")
    

#esta funcion se encarga de enviar la notificacion push al usuario, se llama desde el planificador cada vez que hay una entrevista proxima o pendiente

def enviar_notificacion_push(subscription_str: str, titulo: str, mensaje: str, url_destino: str = "/agenda.html"):
    """Envía una notificación push al usuario utilizando la información de su suscripción."""
    if not VAPID_PRIVATE_KEY:
        print("ERROR: VAPID no configurado")
        return

    if not subscription_str:
        print("INFO: Usuario sin suscripción")
        return

    try:
        subscription_info = json.loads(subscription_str)

        payload = json.dumps({
            "title": titulo,
            "body": mensaje,
            "icon": "/assets/icon-192.png",
            "badge": "/assets/icon-192.png",
            "data": {
                "url": url_destino  # El SW buscará esta clave específicamente
            }
        })

        webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS
        )

        print(f"SUCCESS PUSH: {titulo}")

    except Exception as e:
        print(f"ERROR PUSH: {e}")


#Logica para las notifiacions de Entrevista de la agenda

def gestionar_notificaciones_agenda():
    with Session(engine) as session:
        ahora = datetime.now(nicaragua_tz)
        limite = ahora + timedelta(minutes=30)

        entrevistas = session.exec(
            select(Interview).where(Interview.completada == False)
        ).all()

        print(f"DEBUG: Total entrevistas: {len(entrevistas)}")

        for entrevista in entrevistas:
            try:
                # Construir datetime completo
                fecha_hora = datetime.combine(entrevista.fecha, entrevista.hora)
                fecha_hora = nicaragua_tz.localize(fecha_hora)

                user = session.get(User, entrevista.user_id)

                if not user or not user.push_subscription:
                    continue

                # ===============================
                #  1. RECORDATORIO PRÓXIMO
                # ===============================
                if ahora <= fecha_hora <= limite and not entrevista.notificado_proxima:

                    print(f"DEBUG: Notificando próxima -> {entrevista.id}")

                    enviar_notificacion_push(
                        subscription_str=user.push_subscription,
                        titulo="Entrevista en breve",
                        mensaje=f"Tienes una entrevista a las {entrevista.hora.strftime('%H:%M')}",
                        url_destino="/agenda.html"
                    )

                    entrevista.notificado_proxima = True
                    session.add(entrevista)

                # ===============================
                # 2. ENTREVISTA PENDIENTE
                # ===============================
                elif fecha_hora < ahora and not entrevista.notificado_pendiente:

                    print(f"DEBUG: Notificando pendiente -> {entrevista.id}")

                    enviar_notificacion_push(
                        subscription_str=user.push_subscription,
                        titulo="Entrevista pendiente",
                        mensaje="Tienes una entrevista sin completar.",
                        url_destino="/agenda.html"
                    )

                    entrevista.notificado_pendiente = True
                    session.add(entrevista)

            except Exception as e:
                print(f"ERROR procesando entrevista {entrevista.id}: {e}")

        session.commit()

# Inicializacion del planificador con la zona horaria correcta
scheduler = BackgroundScheduler(timezone=nicaragua_tz)

scheduler.add_job(
    gestionar_notificaciones_agenda,
    'interval',
    minutes=1   
)




@app.on_event("startup")
def iniciar_planificador():
    """
    Inicia el servicio de monitoreo de agenda al arrancar el backend.
    """
    if not scheduler.running:
        scheduler.start()
        print("SISTEMA: Scheduler iniciado")

@app.on_event("shutdown")
def detener_planificador():
    """
    Cierra el servicio de monitoreo de forma segura.
    """
    if scheduler.running:
        scheduler.shutdown()
        print("SISTEMA: Scheduler detenido")


@app.patch("/api/interviews/{interview_id}/complete", status_code=status.HTTP_200_OK)
def finalizar_entrevista(
    interview_id: int,
    calificacion: float,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Marca una entrevista como completada y registra la calificacion final.
    Esto detiene los recordatorios automaticos del planificador.
    """
    # 1. Buscar la entrevista por ID
    entrevista = session.get(Interview, interview_id)
    
    if not entrevista:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Entrevista no encontrada"
        )
    
    # 2. Verificar que la entrevista pertenezca al reclutador actual
    if entrevista.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="No tiene permisos para modificar esta entrevista"
        )
    
    # 3. Actualizar campos
    entrevista.completada = True
    entrevista.calificacion = calificacion
    
    try:
        session.add(entrevista)
        session.commit()
        session.refresh(entrevista)
        
        print(f"LOG: Entrevista {interview_id} marcada como completada por usuario {current_user.id}")
        return {
            "mensaje": "Entrevista finalizada exitosamente",
            "id": entrevista.id,
            "completada": entrevista.completada
        }
    except Exception as e:
        session.rollback()
        print(f"ERROR: No se pudo actualizar la entrevista: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al actualizar el estado de la entrevista en la base de datos"
        )


