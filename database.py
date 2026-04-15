

from sqlmodel import create_engine, SQLModel, Session


DATABASE_URL = "postgresql://postgres:1007031029M@localhost/recruiting_db"

engine = create_engine(DATABASE_URL, echo=True)



def create_db_and_tables():
    """
    Esta función crea todas las tablas en la base de datos.
    Busca todas las clases que heredan de SQLModel (como JobOffer)
    y las crea en PostgreSQL si no existen.
    La llamaremos una sola vez, cuando la aplicación inicia (en main.py).
    """
    SQLModel.metadata.create_all(engine)


def get_session():
    """
    Esta función es un "dependency injector" de FastAPI.
    Cada vez que un endpoint (como /offers/) la necesite, FastAPI
    la ejecutará, creará una 'session', la 'inyectará' en el endpoint,
    y cuando el endpoint termine, cerrará la sesión automáticamente.
    Es la forma moderna de manejar sesiones de BD.
    """
    with Session(engine) as session:
        yield session