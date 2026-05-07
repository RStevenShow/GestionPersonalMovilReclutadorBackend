# =====================================================
#   MARKNICA AI - BACKEND PRINCIPAL (FASTAPI)
#   Proyecto: Sistema Inteligente de Reclutamiento
#   Autor: Ramon Lopez
# =====================================================

# =====================================================
#   IMPORTS PRINCIPALES
# =====================================================

import os
import time
import json

from typing import List
from datetime import datetime, timedelta

from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    UploadFile,
    File,
    status
)

from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import (
    OAuth2PasswordBearer,
    OAuth2PasswordRequestForm
)

from sqlalchemy import func

from sqlmodel import (
    Session,
    select,
    create_engine
)

from jose import JWTError, jwt

from supabase import create_client, Client

from pywebpush import (
    webpush,
    WebPushException
)

from apscheduler.schedulers.background import BackgroundScheduler

from pytz import timezone

# =====================================================
#   IMPORTS INTERNOS DEL PROYECTO
# =====================================================

from database import (
    create_db_and_tables,
    get_session
)

from Models import (
    JobOffer,
    JobOfferCreate,
    JobOfferRead,

    Candidate,
    CandidateRead,

    User,
    UserCreate,
    UserRead,

    Token,

    Interview,
    InterviewCreate,
    InterviewRead
)

from auth_utils import (
    get_password_hash,
    verify_password,
    create_access_token,

    SECRET_KEY,
    ALGORITHM,
    ACCESS_TOKEN_EXPIRE_MINUTES
)

from ai_service import (
    load_models,
    translate_text,
    get_embedding,
    extract_text_from_pdf,
    calculate_similarity,
    generate_rationale,
    extract_email_from_text,
    extract_phone_from_text
)

# =====================================================
#   CONFIGURACIÓN GENERAL
# =====================================================

# Zona horaria oficial del sistema
nicaragua_tz = timezone("America/Managua")

# =====================================================
#   VARIABLES DE ENTORNO
# =====================================================

DATABASE_URL = os.environ.get("DATABASE_URL")

SUPABASE_URL = os.environ.get("SUPABASE_URL")

SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET")

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY")

VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY")

VAPID_EMAIL = os.environ.get(
    "VAPID_EMAIL",
    "mailto:marknicaappmovilreclutador@gmail.com"
)

# =====================================================
#   VALIDACIONES CRÍTICAS DE VARIABLES
# =====================================================

if not DATABASE_URL:
    raise Exception("ERROR: DATABASE_URL no configurado")

if not SUPABASE_URL:
    raise Exception("ERROR: SUPABASE_URL no configurado")

if not SUPABASE_KEY:
    raise Exception("ERROR: SUPABASE_KEY no configurado")

# =====================================================
#   CONFIGURACIÓN DE BASE DE DATOS
# =====================================================

engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True
)

# =====================================================
#   CONFIGURACIÓN DE SUPABASE
# =====================================================

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)

# =====================================================
#   CONFIGURACIÓN DE PUSH NOTIFICATIONS
# =====================================================

VAPID_CLAIMS = {
    "sub": VAPID_EMAIL
}

if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
    print("WARNING: VAPID KEYS NO CONFIGURADAS")

# =====================================================
#   INICIALIZACIÓN FASTAPI
# =====================================================

app = FastAPI(
    title="MarkNica Recruiting AI API",
    version="1.0.0"
)

# =====================================================
#   CONFIGURACIÓN OAUTH2
# =====================================================

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="auth/login"
)

# =====================================================
#   CONFIGURACIÓN CORS
# =====================================================

app.add_middleware(
    CORSMiddleware,

    allow_origins=[
        "*"
    ],

    allow_credentials=True,

    allow_methods=["*"],

    allow_headers=["*"]
)

# =====================================================
#   EVENTO STARTUP
# =====================================================

@app.on_event("startup")
def startup_event():
    """
    Inicializa:
    - Base de datos
    - Modelos IA
    - Scheduler
    """

    print("INICIANDO BACKEND...")

    create_db_and_tables()

    load_models()

    if not scheduler.running:
        scheduler.start()
        print("SCHEDULER INICIADO")

# =====================================================
#   EVENTO SHUTDOWN
# =====================================================

@app.on_event("shutdown")
def shutdown_event():
    """
    Cierra procesos correctamente.
    """

    if scheduler.running:
        scheduler.shutdown()

        print("SCHEDULER DETENIDO")

# =====================================================
#   VALIDACIÓN JWT Y USUARIO ACTUAL
# =====================================================

def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session)
):
    """
    Valida JWT y obtiene el usuario autenticado.
    """

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sesión expirada o inválida",
        headers={"WWW-Authenticate": "Bearer"}
    )

    try:

        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM]
        )

        username: str = payload.get("sub")

        if username is None:
            raise credentials_exception

    except JWTError:
        raise credentials_exception

    user = session.exec(
        select(User).where(
            User.username == username
        )
    ).first()

    if not user:
        raise credentials_exception

    return user

# =====================================================
#   HEALTH CHECK
# =====================================================

@app.get("/")
def root():
    return {
        "status": "online",
        "app": "MarkNica AI Backend"
    }

# =====================================================
#   REGISTRO DE USUARIO
# =====================================================

@app.post(
    "/auth/register",
    response_model=UserRead
)
def register(
    user: UserCreate,
    session: Session = Depends(get_session)
):
    """
    Registra nuevo usuario.
    """

    existing_user = session.exec(
        select(User).where(
            User.username == user.username
        )
    ).first()

    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="El usuario ya existe"
        )

    db_user = User(
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        hashed_password=get_password_hash(user.password)
    )

    session.add(db_user)

    session.commit()

    session.refresh(db_user)

    return db_user

# =====================================================
#   LOGIN
# =====================================================

@app.post(
    "/auth/login",
    response_model=Token
)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session)
):
    """
    Autentica usuario y entrega JWT.
    """

    user = session.exec(
        select(User).where(
            User.username == form_data.username
        )
    ).first()

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Credenciales incorrectas"
        )

    if not verify_password(
        form_data.password,
        user.hashed_password
    ):
        raise HTTPException(
            status_code=401,
            detail="Credenciales incorrectas"
        )

    access_token = create_access_token(
        data={
            "sub": user.username
        }
    )

    return {
        "access_token": access_token,
        "token_type": "bearer"
    }

# =====================================================
#   PERFIL ACTUAL
# =====================================================

@app.get(
    "/users/me",
    response_model=UserRead
)
def read_users_me(
    current_user: User = Depends(get_current_user)
):
    """
    Devuelve datos del usuario actual.
    """

    return current_user

# =====================================================
#   ACTUALIZAR PERFIL
# =====================================================

@app.put(
    "/users/me",
    response_model=UserRead
)
def update_user_me(
    user_data: UserCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Actualiza perfil.
    """

    if user_data.full_name:
        current_user.full_name = user_data.full_name

    if user_data.email:
        current_user.email = user_data.email

    if user_data.password:
        current_user.hashed_password = get_password_hash(
            user_data.password
        )

    session.add(current_user)

    session.commit()

    session.refresh(current_user)

    return current_user

# =====================================================
#   SUBIR FOTO PERFIL
# =====================================================

@app.post("/users/me/photo")
async def upload_profile_photo(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Sube foto de perfil.
    """

    content = await file.read()

    file_path = (
        f"profiles/user_"
        f"{current_user.id}_"
        f"{int(time.time())}.png"
    )

    try:

        supabase.storage.from_("cvs").upload(
            path=file_path,
            file=content,
            file_options={
                "content-type": "image/png"
            }
        )

        public_url = supabase.storage.from_(
            "cvs"
        ).get_public_url(file_path)

        current_user.photo_url = public_url

        session.add(current_user)

        session.commit()

        return {
            "foto_url": public_url
        }

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=f"Error subiendo foto: {str(e)}"
        )

# =====================================================
#   ELIMINAR CUENTA
# =====================================================

@app.delete(
    "/users/me",
    status_code=status.HTTP_204_NO_CONTENT
)
def delete_user_me(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Elimina usuario y datos relacionados.
    """

    session.delete(current_user)

    session.commit()

    return None

# =====================================================
#   CREAR VACANTE
# =====================================================

@app.post(
    "/offers/",
    response_model=JobOfferRead
)
def create_offer(
    offer: JobOfferCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Crea vacante y genera embeddings IA.
    """

    full_context = (
        f"Puesto: {offer.title}. "
        f"Descripcion: {offer.description_original}"
    )

    desc_en = translate_text(full_context)

    vector = get_embedding(desc_en)

    new_offer = JobOffer(
        title=offer.title,
        description_original=offer.description_original,
        description_en=desc_en,
        vector=vector,
        owner_id=current_user.id,
        max_candidatos=offer.max_candidatos,
        estado=offer.estado
    )

    session.add(new_offer)

    session.commit()

    session.refresh(new_offer)

    return new_offer

# =====================================================
#   LISTAR VACANTES
# =====================================================

@app.get(
    "/offers/",
    response_model=List[JobOfferRead]
)
def read_offers(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Obtiene vacantes del usuario.
    """

    offers = session.exec(
        select(JobOffer).where(
            JobOffer.owner_id == current_user.id
        )
    ).all()

    return offers

# =====================================================
#   LEER VACANTE INDIVIDUAL
# =====================================================

@app.get(
    "/offers/{offer_id}",
    response_model=JobOfferRead
)
def read_offer(
    offer_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Obtiene una vacante específica.
    """

    offer = session.get(JobOffer, offer_id)

    if not offer:
        raise HTTPException(
            status_code=404,
            detail="Vacante no encontrada"
        )

    if offer.owner_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="No autorizado"
        )

    return offer

# =====================================================
#   ELIMINAR VACANTE
# =====================================================

@app.delete(
    "/offers/{offer_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
def delete_offer(
    offer_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Elimina vacante.
    """

    offer = session.get(JobOffer, offer_id)

    if not offer:
        raise HTTPException(
            status_code=404,
            detail="Vacante no encontrada"
        )

    if offer.owner_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="No autorizado"
        )

    session.delete(offer)

    session.commit()

    return None

# =====================================================
#   SUBIR CVS Y PROCESAR IA
# =====================================================

@app.post(
    "/offers/{offer_id}/upload_cvs",
    response_model=List[CandidateRead]
)
async def upload_cvs(
    offer_id: int,
    files: List[UploadFile] = File(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Procesa CVs con IA.
    """

    offer = session.get(JobOffer, offer_id)

    if not offer:
        raise HTTPException(
            status_code=404,
            detail="Vacante no encontrada"
        )

    if offer.owner_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="No autorizado"
        )

    results = []

    for file in files:

        content = await file.read()

        safe_name = (
            f"{current_user.id}/"
            f"{int(time.time())}_"
            f"{file.filename}"
        )

        try:

            supabase.storage.from_("cvs").upload(
                path=safe_name,
                file=content,
                file_options={
                    "content-type": "application/pdf"
                }
            )

            public_url = supabase.storage.from_(
                "cvs"
            ).get_public_url(safe_name)

        except Exception as e:

            raise HTTPException(
                status_code=500,
                detail=f"Error subiendo archivo: {str(e)}"
            )

        text_es = extract_text_from_pdf(content)

        email = extract_email_from_text(text_es)

        phone = extract_phone_from_text(text_es)

        text_en = translate_text(text_es)

        vec_cv = get_embedding(text_en)

        score = calculate_similarity(
            vec_cv,
            offer.vector
        )

        rationale = generate_rationale(
            text_en,
            offer.description_en
        )

        candidate = Candidate(
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

        session.add(candidate)

        results.append(candidate)

    session.commit()

    results.sort(
        key=lambda x: x.match_score,
        reverse=True
    )

    return results

# =====================================================
#   CREAR ENTREVISTA
# =====================================================

@app.post(
    "/interviews/",
    response_model=InterviewRead
)
def create_interview(
    interview: InterviewCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Agenda entrevista.
    """

    db_interview = Interview(
        **interview.dict(),
        user_id=current_user.id
    )

    session.add(db_interview)

    session.commit()

    session.refresh(db_interview)

    return db_interview

# =====================================================
#   LISTAR ENTREVISTAS
# =====================================================

@app.get("/interviews/")
def read_interviews(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Lista entrevistas.
    """

    interviews = session.exec(
        select(Interview).where(
            Interview.user_id == current_user.id
        )
    ).all()

    return interviews

# =====================================================
#   SISTEMA PUSH
# =====================================================

@app.post("/api/save-subscription")
def save_subscription(
    subscription: dict,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Guarda suscripción push.
    """

    current_user.push_subscription = json.dumps(
        subscription
    )

    session.add(current_user)

    session.commit()

    return {
        "ok": True
    }

# =====================================================
#   ENVÍO PUSH
# =====================================================

def enviar_notificacion_push(
    subscription_str: str,
    titulo: str,
    mensaje: str,
    url_destino: str = "/agenda.html"
):
    """
    Envía notificación push.
    """

    if not subscription_str:
        return

    try:

        subscription_info = json.loads(
            subscription_str
        )

        payload = json.dumps({
            "title": titulo,
            "body": mensaje,
            "icon": "/assets/icon-192.png",
            "badge": "/assets/icon-192.png",
            "data": {
                "url": url_destino
            }
        })

        webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS
        )

    except Exception as e:

        print(f"ERROR PUSH: {e}")

# =====================================================
#   SCHEDULER AGENDA
# =====================================================

def gestionar_notificaciones_agenda():
    """
    Verifica entrevistas próximas.
    """

    with Session(engine) as session:

        ahora = datetime.now(nicaragua_tz)

        limite = ahora + timedelta(minutes=30)

        entrevistas = session.exec(
            select(Interview).where(
                Interview.completada == False
            )
        ).all()

        for entrevista in entrevistas:

            try:

                fecha_hora = datetime.combine(
                    entrevista.fecha,
                    entrevista.hora
                )

                fecha_hora = nicaragua_tz.localize(
                    fecha_hora
                )

                user = session.get(
                    User,
                    entrevista.user_id
                )

                if not user:
                    continue

                if not user.push_subscription:
                    continue

                if (
                    ahora <= fecha_hora <= limite
                    and not entrevista.notificado_proxima
                ):

                    enviar_notificacion_push(
                        subscription_str=user.push_subscription,
                        titulo="Entrevista próxima",
                        mensaje="Tienes una entrevista programada",
                        url_destino="/agenda.html"
                    )

                    entrevista.notificado_proxima = True

                    session.add(entrevista)

            except Exception as e:

                print(f"ERROR ENTREVISTA: {e}")

        session.commit()

# =====================================================
#   CONFIGURACIÓN SCHEDULER
# =====================================================

scheduler = BackgroundScheduler(
    timezone=nicaragua_tz
)

scheduler.add_job(
    gestionar_notificaciones_agenda,
    "interval",
    minutes=1
)

# =====================================================
#   FINALIZAR ENTREVISTA
# =====================================================

@app.patch("/api/interviews/{interview_id}/complete")
def finalizar_entrevista(
    interview_id: int,
    calificacion: float,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Finaliza entrevista.
    """

    entrevista = session.get(
        Interview,
        interview_id
    )

    if not entrevista:
        raise HTTPException(
            status_code=404,
            detail="Entrevista no encontrada"
        )

    if entrevista.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="No autorizado"
        )

    entrevista.completada = True

    entrevista.calificacion = calificacion

    session.add(entrevista)

    session.commit()

    session.refresh(entrevista)

    return {
        "mensaje": "Entrevista completada"
    }