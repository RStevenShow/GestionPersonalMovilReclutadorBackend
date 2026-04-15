import os
from sqlmodel import create_engine, SQLModel, Session

# 1. Intentamos obtener la URL de Render (Supabase). 
# Si no existe, usamos la de tu localhost por si quieres seguir probando en tu PC.
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    DATABASE_URL = "postgresql://postgres:1007031029M@localhost/recruiting_db"

# 2. IMPORTANTE: Supabase/PostgreSQL en la nube a veces requiere 
# configuraciones adicionales para manejar conexiones inactivas.
engine = create_engine(
    DATABASE_URL, 
    echo=True,
    pool_pre_ping=True  # Verifica que la conexión esté viva antes de usarla
)

def create_db_and_tables():
    """
    Crea las tablas en Supabase basándose en tus modelos.
    """
    SQLModel.metadata.create_all(engine)

def get_session():
    """
    Genera sesiones para los endpoints de FastAPI.
    """
    with Session(engine) as session:
        yield session