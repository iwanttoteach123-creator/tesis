from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
load_dotenv()
from pydantic import BaseModel
from fastapi import HTTPException
from fastapi.responses import JSONResponse
import psycopg2
from psycopg2.extras import RealDictCursor
import os
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("⚠️ DATABASE_URL no está definida. Usando SQLite local para desarrollo.")
    DATABASE_URL = "sqlite:///./dev.db"
class LoginBody(BaseModel):
    correo: str
    clave: str
app = FastAPI()

def connect_db():
    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=RealDictCursor
    )

# Función para autenticar un usuario y obtener cursos inscritos
def login(correo: str, clave: str):
    try:
        with connect_db() as connection:
            cursor = connection.cursor()


            # Consulta para verificar las credenciales del usuario
            cursor.execute("SELECT * FROM usuario WHERE correo = %s AND clave = %s", (correo, clave))
            user = cursor.fetchone()

            if not user:
                raise HTTPException(status_code=401, detail="Credenciales inválidas")

            # Consulta para obtener los cursos inscritos por el usuario
            cursor.execute("SELECT c.id, c.nombre FROM curso c JOIN usuario_curso uc ON c.id = uc.id_curso WHERE uc.id_usuario = %s", (user['id'],))
            cursos = cursor.fetchall()

            user['cursos'] = cursos

            return user

    except Exception as e:
        print(f"Error al autenticar usuario en la base de datos: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")


