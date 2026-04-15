import os
from sqlmodel import create_engine, SQLModel, Session
from dotenv import load_dotenv

# Cargar variables locales del archivo .env
load_dotenv()

# Obtener URL desde las variables de entorno de Render o local .env
DATABASE_URL = os.getenv("DATABASE_URL")

# Correccion de prefijo necesaria para SQLAlchemy en la nube
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)

# Respaldo para desarrollo local en tu PC
if not DATABASE_URL:
    DATABASE_URL = "postgresql://postgres:1007031029M@localhost/recruiting_db"

# Configuracion del motor de base de datos
engine = create_engine(
    DATABASE_URL, 
    echo=True,        # Muestra las consultas SQL en la consola (util para debugear)
    pool_pre_ping=True # Evita errores de "conexion perdida" con Supabase
)

def create_db_and_tables():
    """
    Sincroniza los modelos con la base de datos de Supabase.
    Crea las tablas si no existen.
    """
    SQLModel.metadata.create_all(engine)

def get_session():
    """
    Provee una sesion de base de datos para cada peticion de FastAPI.
    """
    with Session(engine) as session:
        yield session