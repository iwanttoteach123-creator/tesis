# =========================
# 0) Cargar .env (backend/.env)
# =========================
from pathlib import Path
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"   # -> backend/.env
load_dotenv(ENV_PATH)

# =========================
# 1) Imports estándar / terceros
# =========================
import os
import io
import re
import json
import base64
import time
import asyncio
import zipfile
import tarfile
import threading
import logging
import smtplib
import imghdr
import requests
import schedule
import psycopg2
import fitz
import pdfkit
import httpx

from datetime import datetime, timedelta
from typing import Optional, List

from pydantic import BaseModel

from email import encoders
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart

from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import (
    FastAPI, HTTPException, Request, Depends, Response,
    UploadFile, File, Form, BackgroundTasks
)
from psycopg2.extras import RealDictCursor

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# =========================
# 2) Imports internos del proyecto
# =========================
from .gpt_api import *               # si lo usas aquí
from .autenticacion import login
from .services.google_drive_oauth import GoogleDriveOAuth
from ..api import router as api_router

# =========================
# 3) App
# =========================
app = FastAPI()
app.include_router(api_router, prefix="/api")

# =========================
# 4) Modelos (ANTES de endpoints)
# =========================
class UsuarioCreate(BaseModel):
    nombre: str
    clave: str
    correo: str
    direccion: str
    numero_cel: str

class LoginBody(BaseModel):
    correo: str
    clave: str

# =========================
# 5) Config / envs (no crashear en import-time)
# =========================
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("⚠️ DATABASE_URL no está definida. Usando SQLite local para desarrollo.")
    DATABASE_URL = "sqlite:///./dev.db"

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_INSTITUCIONAL = os.getenv("EMAIL_INSTITUCIONAL")
EMAIL_PASSWORDI = os.getenv("EMAIL_PASSWORDI")

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))

EMAIL_ENABLED = bool(EMAIL_SENDER and EMAIL_PASSWORD)
if not EMAIL_ENABLED:
    print("⚠️ Email deshabilitado: faltan EMAIL_SENDER y/o EMAIL_PASSWORD (no se cae el server).")

IS_LOCAL = os.getenv("IS_LOCAL", "1") == "1"

# ⚠️ NO hardcodees credenciales en código: usa env siempre.
# Si quieres mantenerlo por ahora, déjalo, pero esto NO es para producción.

# =========================
# 6) Logging
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =========================
# 7) CORS (solo CORSMiddleware; NO middleware manual)
# =========================
origins = [
    "http://localhost:5173",
    "http://localhost:3000",
    "https://whimsical-brigadeiros-eed9f9.netlify.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# 8) DB helper
# =========================
def connect_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no configurada")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

is_local = os.getenv("IS_LOCAL", "1") == "1"

# =========================
# 9) Google Drive service (NO debe botar el server)
# =========================
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
CLIENT_SECRETS_FILE = "credentials.json"

try:
    drive_service = GoogleDriveOAuth()
    logger.info("✅ Google Drive Service inicializado")
except Exception as e:
    logger.error(f"❌ Error inicializando Google Drive: {e}")
    drive_service = None

# =========================
# 10) Endpoints Drive (los mantengo)
# =========================
@app.post("/api/subir-drive")
async def subir_a_drive(
    file: UploadFile = File(...),
    dias_expiracion: str = Form("7"),
    permisos: str = Form("lector")
):
    try:
        if not drive_service:
            raise HTTPException(status_code=500, detail="Servicio de Drive no disponible")

        logger.info(f"📥 Recibiendo archivo: {file.filename}")

        file_content = await file.read()
        file_size_mb = len(file_content) / (1024 * 1024)
        logger.info(f"📊 Tamaño del archivo: {file_size_mb:.2f} MB")

        result = drive_service.upload_file(file_content)

        if result.get("success"):
            logger.info(f"✅ Archivo subido exitosamente: {result.get('file_name')}")
            return {
                "success": True,
                "message": "Archivo subido a Google Drive",
                "drive_link": result.get("drive_link"),
                "file_id": result.get("file_id"),
                "file_name": result.get("file_name"),
                "file_size_mb": result.get("file_size_mb"),
                "info": "El archivo estará disponible indefinidamente en Google Drive",
            }

        logger.error(f"❌ Error subiendo archivo: {result.get('error')}")
        raise HTTPException(status_code=500, detail=result.get("error", "Error desconocido"))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error en /api/subir-drive: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health():
    return {"status": "ok"}
# ==========================
@app.get("/api/test-drive")
async def test_drive():
    try:
        if not drive_service:
            return {"success": False, "error": "Servicio de Drive no inicializado"}

        test_content = b"Este es un archivo de prueba para Google Drive - " + datetime.now().isoformat().encode()
        result = drive_service.upload_file(test_content, "test_conexion.txt")

        return {
            "success": result.get("success", False),
            "message": "Prueba de Google Drive completada",
            "result": result if result.get("success") else {"error": result.get("error")},
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/drive-status")
async def drive_status():
    return {"service_available": drive_service is not None, "timestamp": datetime.now().isoformat()}


#########################
# Ruta para crear un nuevo usuario
@app.post("/registro")
async def crear_usuario(usuario: UsuarioCreate):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        # Consulta para insertar un nuevo usuario en la tabla
        insert_query = """
            INSERT INTO usuario (nombre, tipo, clave, correo, direccion, numero_cel)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        cursor.execute(insert_query, (
            usuario.nombre,
            1,  # tipo = 1 para alumnos
            usuario.clave,
            usuario.correo,
            usuario.direccion,
            usuario.numero_cel
        ))

        conn.commit()
        cursor.close()
        conn.close()

        return {"message": "Usuario creado exitosamente"}

    except Exception as e:
        print(f"Error al crear usuario: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

################################################


#######################################
@app.put("/perfil/{user_id}")
async def update_profile(user_id: int, request: Request):
    data = await request.json()
    direccion = data.get('direccion')
    numero_cel = data.get('numero_cel')

    try:
        conn = connect_db()
        cursor = conn.cursor()

        # Consulta para actualizar los datos del usuario
        update_query = """
            UPDATE usuario
            SET direccion = %s, numero_cel = %s
            WHERE id = %s
        """
        cursor.execute(update_query, (direccion, numero_cel, user_id))

        conn.commit()
        cursor.close()
        conn.close()

        return {"message": "Perfil actualizado exitosamente"}

    except Exception as e:
        print(f"Error al actualizar el perfil: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
#######################################3


# Función para verificar la sesión del usuario usando cookies
async def get_user_id(request: Request):
    user_id = request.cookies.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Usuario no autenticado")
    return int(user_id)

# Modifica tu login para que devuelva un token
@app.post("/login")
async def user_login(body: LoginBody):
    user = login(body.correo, body.clave)

    response = JSONResponse(content={"message": "Login exitoso", "user": user})
    response.set_cookie(
        key="user_id",
        value=str(user["id"]),
        httponly=True,
        secure=False,   # True cuando sea HTTPS en producción
        samesite="lax"
    )
    return response
@app.post("/logout")
async def user_logout(response: Response):
    # Elimina la cookie de user_id configurando su fecha de expiración en el pasado
    response.delete_cookie(key="user_id")
    
    return JSONResponse(content={"message": "Sesión cerrada con éxito"}, status_code=200)

# Ruta para obtener el ID del usuario de la cookie
@app.get("/user_id")
async def get_current_user(request: Request):
    user_id = await get_user_id(request)
    return {"user_id": user_id}

@app.get("/usuario/{user_id}/cursos")
async def get_user_courses(user_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        
        # Consulta para obtener los cursos en los que está inscrito el usuario
        cursor.execute("""
            SELECT c.id, c.nombre 
            FROM curso c 
            JOIN usuario_curso uc ON c.id = uc.id_curso 
            WHERE uc.id_usuario = %s
        """, (user_id,))
        cursos = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        return cursos
    
    except Exception as e:
        print(f"Error al obtener cursos del usuario: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
    

@app.get("/curso/{curso_id}/nombre")
async def get_curso_nombre(curso_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        
        # Obtener el nombre del curso
        cursor.execute("SELECT nombre FROM curso WHERE id = %s", (curso_id,))
        curso = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        if not curso:
            raise HTTPException(status_code=404, detail="Curso no encontrado")
        
        # Retornar el nombre del curso
        return {"nombre_curso": curso['nombre']}
    
    except Exception as e:
        print(f"Error al obtener nombre del curso: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

@app.get("/curso-profesor/{curso_id}/nombre")
async def get_curso_profesor_nombre(curso_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        
        # Obtener el nombre del curso
        cursor.execute("SELECT nombre FROM curso WHERE id = %s", (curso_id,))
        curso = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        if not curso:
            raise HTTPException(status_code=404, detail="Curso no encontrado")
        
        # Retornar el nombre del curso
        return {"nombre_curso": curso['nombre']}
    
    except Exception as e:
        print(f"Error al obtener nombre del curso para profesor: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
    
@app.get("/curso/{curso_id}/unidades")
async def get_unidades_curso_alumno(curso_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        
        # Obtener las unidades asociadas al curso
        cursor.execute("SELECT id, nombre FROM unidad WHERE id_curso = %s", (curso_id,))
        unidades = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        # Retornar las unidades asociadas al curso
        return {"unidades": unidades}
    
    except Exception as e:
        print(f"Error al obtener unidades del curso para alumno: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

# Ruta para obtener las unidades de un curso para profesores
@app.get("/curso-profesor/{curso_id}/unidades")
async def get_unidades_curso_profesor(curso_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        
        # Obtener las unidades asociadas al curso
        cursor.execute("SELECT id, nombre FROM unidad WHERE id_curso = %s", (curso_id,))
        unidades = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        # Retornar las unidades asociadas al curso
        return {"unidades": unidades}
    
    except Exception as e:
        print(f"Error al obtener unidades del curso para profesor: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

# Función para crear un nuevo curso con una unidad inicial
@app.post("/curso")
async def crear_curso(request: Request):
    data = await request.json()
    nombre_curso = data.get('nombre_curso')
    id_usuario = data.get('id_usuario')
    #------ crear asistente y vector
    nombre_asistente = f"{nombre_curso}_"

    # Llamada a la API para crear un nuevo asistente
    url = f"{url_api}/create-assistant/"
    params = {"name": nombre_asistente}
    response = requests.post(url, params=params)
    if response.status_code == 200:
        assistant_id = response.json()  # Obtener el assistant_id de la respuesta
    if not nombre_curso:
        raise HTTPException(status_code=400, detail="El nombre de la unidad es requerido")
    
     # Llamada a la API para crear el vector
    url_create_vector = f"{url_api}/create-vector/{assistant_id}"
    response_vector = requests.post(url_create_vector)

    if response_vector.status_code == 200:
        vector_id = response_vector.json()  # Obtener el vector_id de la respuesta
    else:
        raise HTTPException(status_code=500, detail="Error al crear el vector")
    #------

    try:
        conn = connect_db()
        cursor = conn.cursor()

        # Insertar el nuevo curso
        cursor.execute(
            "INSERT INTO curso (nombre) VALUES (%s) RETURNING id",
            (nombre_curso,)
        )
        curso_id = cursor.fetchone()["id"]

        # Insertar la unidad asociada al curso (Unidad 1)
        cursor.execute(
            """
            INSERT INTO unidad (nombre, id_curso, assistant_id, vector_id)
            VALUES (%s, %s, %s, %s)
            """,
            ('Unidad 1', curso_id, assistant_id, vector_id)
        )

        # Insertar la asociación usuario_curso
        cursor.execute(
            """
            INSERT INTO usuario_curso (id_usuario, id_curso)
            VALUES (%s, %s)
            """,
            (id_usuario, curso_id)
        )

        conn.commit()
        return JSONResponse(
            status_code=200,
            content={"message": "Curso creado exitosamente", "curso_id": curso_id}
        )

    finally:
        try:
            cursor.close()
            conn.close()
        except:
            pass



# Nueva ruta para actualizar el nombre del curso
@app.put("/curso/{curso_id}")
async def actualizar_curso(curso_id: int, request: Request):
    data = await request.json()
    nuevo_nombre = data.get('nombre_curso')

    if not nuevo_nombre:
        raise HTTPException(status_code=400, detail="El nombre del curso es requerido")

    try:
        conn = connect_db()
        cursor = conn.cursor()

        # Actualizar el nombre del curso
        cursor.execute("UPDATE curso SET nombre = %s WHERE id = %s", (nuevo_nombre, curso_id))

        conn.commit()
        cursor.close()
        conn.close()

        return JSONResponse(status_code=200, content={"message": "Curso actualizado exitosamente"})

    except Exception as e:
        print(f"Error al actualizar el curso: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")


# Ruta para obtener las actividades de una unidad específica
@app.get("/unidad/{unidad_id}/actividades")
async def get_actividades_por_unidad(unidad_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        
        # Consulta para obtener las actividades de la unidad especificada
        cursor.execute("SELECT id, titulo, descripcion, estado, fecha_inicio, fecha_cierre, hora_inicio, hora_cierre FROM actividad WHERE id_unidad = %s", (unidad_id,))
        actividades = cursor.fetchall()
        
        # Formatear la hora_cierre
        for actividad in actividades:
            if isinstance(actividad['hora_cierre'], timedelta):
                seconds = actividad['hora_cierre'].total_seconds()
                hours = int(seconds // 3600)
                minutes = int((seconds % 3600) // 60)
                seconds = int(seconds % 60)
                actividad['hora_cierre'] = f"{hours:02}:{minutes:02}:{seconds:02}"

        for actividad in actividades:
            if isinstance(actividad['hora_inicio'], timedelta):
                seconds = actividad['hora_inicio'].total_seconds()
                hours = int(seconds // 3600)
                minutes = int((seconds % 3600) // 60)
                seconds = int(seconds % 60)
                actividad['hora_inicio'] = f"{hours:02}:{minutes:02}:{seconds:02}"

        cursor.close()
        conn.close()
        
        # Verificar si se encontraron actividades
        if not actividades:
            return {"message": "No se encontraron actividades para la unidad especificada"}
        
        
        # Retornar las actividades como JSON
        return {"actividades": actividades}
    
    except Exception as e:
        print(f"Error al obtener actividades por unidad: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

# Ruta para eliminar un curso
@app.delete("/curso/{curso_id}")
async def eliminar_curso(curso_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        # Obtener todas las unidades vinculadas a este curso
        cursor.execute("SELECT id, assistant_id, vector_id FROM unidad WHERE id_curso = %s", (curso_id,))
        unidades = cursor.fetchall()

        # Recorre todas las unidades vinculadas para eliminar sus asistentes y vectores
        for unidad in unidades:
            unidad_id, assistant_id, vector_id = unidad

            # Eliminar el asistente asociado
            if assistant_id:
                try:
                    # Llamada HTTP para eliminar el asistente en la API en el puerto 8001
                    assistant_delete_url = f"{url_api}/delete-assistant/{assistant_id}"
                    response = requests.delete(assistant_delete_url)
                    if response.status_code == 200:
                        print(f"Asistente {assistant_id} eliminado correctamente")
                    else:
                        print(f"Error al eliminar el asistente {assistant_id}: {response.status_code}, {response.text}")
                except Exception as e:
                    print(f"Error al eliminar el asistente {assistant_id}: {e}")

            # Eliminar el vector asociado
            if vector_id:
                try:
                    # Llamada HTTP para eliminar el vector en la API en el puerto 8001
                    vector_delete_url = f"{url_api}/delete-vector/{vector_id}/"
                    response = requests.delete(vector_delete_url)
                    if response.status_code == 200:
                        print(f"Vector {vector_id} eliminado correctamente")
                    else:
                        print(f"Error al eliminar el vector {vector_id}: {response.status_code}, {response.text}")
                except Exception as e:
                    print(f"Error al eliminar el vector {vector_id}: {e}")

        # Eliminar todas las unidades vinculadas al curso
        cursor.execute("DELETE FROM unidad WHERE id_curso = %s", (curso_id,))

        # Eliminar el curso de la tabla curso
        cursor.execute("DELETE FROM curso WHERE id = %s", (curso_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return JSONResponse(status_code=200, content={"message": "Curso y sus datos vinculados eliminados correctamente"})

    except Exception as e:
        print(f"Error al eliminar el curso: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

    
@app.post("/curso/{curso_id}/unidad")    
async def crear_unidad(curso_id: int, request: Request):
    data = await request.json()
    nombre_unidad = data.get('nombre_unidad')
    
    # NO LA CAGUES CLAUDIO AROS
    nombre_asistente = f"{nombre_unidad}_"

    # Llamada a la API para crear un nuevo asistente
    url = f"{url_api}/create-assistant/"
    params = {"name": nombre_asistente}
    response = requests.post(url, params=params)
    if response.status_code == 200:
        assistant_id = response.json()  # Obtener el assistant_id de la respuesta
    if not nombre_unidad:
        raise HTTPException(status_code=400, detail="El nombre de la unidad es requerido")
    
    # Llamada a la API para crear el vector
    url_create_vector = f"{url_api}/create-vector/{assistant_id}"
    response_vector = requests.post(url_create_vector)

    if response_vector.status_code == 200:
        vector_id = response_vector.json()
    else:
        raise HTTPException(status_code=500, detail="Error al crear el vector")

    try:
        conn = connect_db()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO unidad (nombre, id_curso, assistant_id, vector_id)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (nombre_unidad, curso_id, assistant_id, vector_id)
        )

        unidad_id = cursor.fetchone()["id"]

        conn.commit()
        return JSONResponse(
            status_code=201,
            content={"message": "Unidad creada exitosamente", "unidad_id": unidad_id}
        )

    finally:
        try:
            cursor.close()
            conn.close()
        except:
            pass



# Ruta para actualizar el nombre de una unidad
@app.put("/unidad/{unidad_id}")
async def actualizar_nombre_unidad(unidad_id: int, request: Request):
    data = await request.json()
    nuevo_nombre = data.get('nombre_unidad')

    if not nuevo_nombre:
        raise HTTPException(status_code=400, detail="El nombre de la unidad es requerido")

    try:
        conn = connect_db()
        cursor = conn.cursor()

        # Actualizar el nombre de la unidad
        cursor.execute("UPDATE unidad SET nombre = %s WHERE id = %s", (nuevo_nombre, unidad_id))

        conn.commit()
        cursor.close()
        conn.close()

        return JSONResponse(status_code=200, content={"message": "Nombre de la unidad actualizado exitosamente"})

    except Exception as e:
        print(f"Error al actualizar el nombre de la unidad: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

# Ruta para actualizar el nombre de una unidad
@app.put("/unidad/{unidad_id}")
async def actualizar_nombre_unidad(unidad_id: int, request: Request):
    data = await request.json()
    nuevo_nombre = data.get('nombre_unidad')

    if not nuevo_nombre:
        raise HTTPException(status_code=400, detail="El nombre de la unidad es requerido")

    try:
        conn = connect_db()
        cursor = conn.cursor()

        # Actualizar el nombre de la unidad
        cursor.execute("UPDATE unidad SET nombre = %s WHERE id = %s", (nuevo_nombre, unidad_id))

        conn.commit()
        cursor.close()
        conn.close()

        return JSONResponse(status_code=200, content={"message": "Nombre de la unidad actualizado exitosamente"})

    except Exception as e:
        print(f"Error al actualizar el nombre de la unidad: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
    

@app.delete("/unidad/{unidad_id}")
async def eliminar_unidad(unidad_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        # Obtener el assistant_id y el vector_id vinculados a la unidad antes de eliminarla
        cursor.execute("SELECT assistant_id, vector_id FROM unidad WHERE id = %s", (unidad_id,))
        result = cursor.fetchone()
        if result:
            assistant_id, vector_id = result
        else:
            return JSONResponse(status_code=404, content={"message": "Unidad no encontrada"})

        # Eliminar el asistente vinculado si existe
        if assistant_id:
            try:
                # Llamada HTTP para eliminar el asistente usando la API en el puerto 8001
                assistant_delete_url = f"{url_api}/delete-assistant/{assistant_id}"
                response = requests.delete(assistant_delete_url)
                if response.status_code == 200:
                    print(f"Asistente {assistant_id} eliminado correctamente")
                else:
                    print(f"Error al eliminar el asistente {assistant_id}: {response.status_code}, {response.text}")
            except Exception as e:
                print(f"Error al eliminar el asistente {assistant_id}: {e}")

        # Eliminar el vector vinculado si existe
        if vector_id:
            try:
                # Llamada HTTP para eliminar el vector usando la API en el puerto 8001
                vector_delete_url = f"{url_api}/delete-vector/{vector_id}/"
                response = requests.delete(vector_delete_url)
                if response.status_code == 200:
                    print(f"Vector {vector_id} eliminado correctamente")
                else:
                    print(f"Error al eliminar el vector {vector_id}: {response.status_code}, {response.text}")
            except Exception as e:
                print(f"Error al eliminar el vector {vector_id}: {e}")

        # Eliminar la unidad y sus actividades asociadas (en cascada)
        cursor.execute("DELETE FROM unidad WHERE id = %s", (unidad_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return JSONResponse(status_code=200, content={"message": "Unidad, actividades, asistente, y vector eliminados correctamente"})

    except Exception as e:
        print(f"Error al eliminar la unidad y actividades asociadas: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

# Ruta para crear una nueva actividad en una unidad específica
# Ruta para crear una nueva actividad en una unidad específica con un archivo PDF
@app.post("/unidad/{unidad_id}/actividad")
async def crear_actividad(unidad_id: int, titulo: str = Form(...), descripcion: str = Form(...), estado: str = Form(...),
                          fecha_inicio: str = Form(None), fecha_cierre: str = Form(None), 
                          hora_inicio: str = Form(None), hora_cierre: str = Form(None),
                          archivo_pdf: UploadFile = File(...)):
    # Verificar que el archivo subido es un PDF
    if archivo_pdf.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF")

    # Leer el archivo directamente desde la memoria (sin almacenarlo en el sistema de archivos)
    archivo_pdf_bytes = await archivo_pdf.read()

    # Extraer el texto del PDF directamente desde los bytes del archivo
    texto_pdf = extract_text_from_pdf(archivo_pdf_bytes)

    # Enviar el texto extraído al modelo GPT para obtener los requerimientos
    requerimientos_text = req_desafio_to_json(texto_pdf)

    # Procesar el texto devuelto para extraer solo el JSON
    try:
        json_start = requerimientos_text.find('{')
        json_end = requerimientos_text.rfind('}') + 1
        requerimientos_json = requerimientos_text[json_start:json_end]

        # Cargarlo como JSON para verificar que es válido
        requerimientos_data = json.loads(requerimientos_json)

    except ValueError:
        raise HTTPException(status_code=400, detail="El texto recibido no es un JSON válido")

    # Convertir a JSON para almacenar en la base de datos
    requerimientos_json = json.dumps(requerimientos_data)


    # Insertar en la base de datos
    try:
        conn = connect_db()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO actividad (
                titulo, descripcion, id_unidad, estado,
                fecha_inicio, fecha_cierre, hora_inicio, hora_cierre, requerimientos
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                titulo, descripcion, unidad_id, estado,
                fecha_inicio, fecha_cierre, hora_inicio, hora_cierre, requerimientos_json
            )
        )

        actividad_id = cursor.fetchone()["id"]

        conn.commit()
        return JSONResponse(
            status_code=201,
            content={"message": "Actividad creada exitosamente", "actividad_id": actividad_id}
        )

    finally:
        try:
            cursor.close()
            conn.close()
        except:
            pass
  





# Ruta para actualizar una actividad
@app.put("/actividad/{actividad_id}")
async def actualizar_actividad(actividad_id: int, request: Request):
    data = await request.json()
    nuevo_titulo = data.get('titulo')
    nueva_descripcion = data.get('descripcion')
    nuevo_estado = data.get('estado')

    if not nuevo_titulo or not nueva_descripcion or not nuevo_estado:
        raise HTTPException(status_code=400, detail="Se requieren tanto título como descripción para actualizar una actividad")

    try:
        conn = connect_db()
        cursor = conn.cursor()

        # Actualizar la actividad en la base de datos
        cursor.execute("UPDATE actividad SET titulo = %s, descripcion = %s, estado = %s WHERE id = %s", (nuevo_titulo, nueva_descripcion, nuevo_estado, actividad_id))

        conn.commit()
        cursor.close()
        conn.close()

        return JSONResponse(status_code=200, content={"message": "Actividad actualizada exitosamente"})

    except Exception as e:
        print(f"Error al actualizar la actividad: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

# Ruta para eliminar una actividad
@app.delete("/actividad/{actividad_id}")
async def eliminar_actividad(actividad_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        # Eliminar la actividad de la base de datos
        cursor.execute("DELETE FROM actividad WHERE id = %s", (actividad_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return JSONResponse(status_code=200, content={"message": "Actividad eliminada correctamente"})

    except Exception as e:
        print(f"Error al eliminar la actividad: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

@app.delete("/respuesta/{respuesta_id}")
async def eliminar_respuesta(respuesta_id: int):
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()

        # Eliminar la respuesta de la base de datos usando solo el id de la respuesta
        cursor.execute("DELETE FROM respuesta WHERE id = %s", (respuesta_id,))
        conn.commit()

        cursor.close()
        conn.close()

        return JSONResponse(status_code=200, content={"message": "Respuesta eliminada correctamente"})

    except Exception as e:
        print(f"Error al eliminar la respuesta: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")


###############################################################3
#se agrega el manejo de la fecha de solicitud
# Ruta para obtener respuestas de una actividad específica
@app.get("/actividad/{actividad_id}/respuestas")
async def get_respuestas_actividad(actividad_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        
        # Consulta para obtener las respuestas de la actividad, el nombre de usuario, el ID del curso y la fecha
        cursor.execute("""
            SELECT respuesta.id, respuesta.archivo, respuesta.feedback, respuesta.fecha, usuario.nombre AS nombre_usuario, actividad.id_unidad, unidad.id_curso
            FROM respuesta
            JOIN usuario ON respuesta.id_usuario = usuario.id
            JOIN actividad ON respuesta.id_actividad = actividad.id
            JOIN unidad ON actividad.id_unidad = unidad.id
            WHERE respuesta.id_actividad = %s
            ORDER BY usuario.nombre, respuesta.fecha DESC
        """, (actividad_id,))
        
        respuestas = cursor.fetchall()
        
        # Consulta para obtener el ID del curso asociado a la actividad
        cursor.execute("""
            SELECT DISTINCT unidad.id_curso
            FROM actividad
            JOIN unidad ON actividad.id_unidad = unidad.id
            WHERE actividad.id = %s
        """, (actividad_id,))
        
        curso_id = cursor.fetchone()['id_curso']  # Obtener el ID del curso
        
        # Si no se encontraron respuestas, devolver una lista vacía
        if not respuestas:
            return {"respuestas": [], "curso_id": curso_id}
        
        # Retornar las respuestas como JSON con los nombres de usuario incluidos
        return {"respuestas": respuestas, "curso_id": curso_id}
    
    except Exception as e:
        print(f"Error al obtener respuestas de la actividad: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
    
    finally:
        cursor.close()
        conn.close()
#################################################################################

############################################################
@app.put("/respuesta/{respuesta_id}")
async def actualizar_feedback(respuesta_id: int, feedback: str = Form(...)):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        # Actualizar el feedback en la base de datos
        update_query = """
            UPDATE respuesta
            SET feedback = %s
            WHERE id = %s
        """
        cursor.execute(update_query, (feedback, respuesta_id))
        conn.commit()

        cursor.execute("SELECT feedback FROM respuesta WHERE id = %s", (respuesta_id,))
        updated_feedback = cursor.fetchone()[0]

        return {"message": "Feedback actualizado correctamente", "feedback": updated_feedback}

    except Exception as e:
        print(f"Error al actualizar el feedback: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
    finally:
        cursor.close()
        conn.close()

#############################################################


@app.get("/unidad/{unidad_id}/corpus")
async def get_corpus_unidad(unidad_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()


        # Consulta para obtener el ID del curso desde la unidad
        cursor.execute("""
            SELECT id_curso 
            FROM unidad 
            WHERE id = %s
        """, (unidad_id,))
        unidad = cursor.fetchone()
        
        # Si no se encuentra la unidad, devolver un error
        if not unidad:
            raise HTTPException(status_code=404, detail="Unidad no encontrada")
        
        curso_id = unidad['id_curso']
        
        # Consulta para obtener los corpus de la unidad
        cursor.execute("""
            SELECT corpus.id, corpus.titulo, corpus.material
            FROM corpus
            WHERE corpus.id_unidad = %s
        """, (unidad_id,))

        corpus = cursor.fetchall()

        # Retornar los corpus y el curso_id, aunque corpus esté vacío
        return {"corpus": corpus, "curso_id": curso_id}
    
    except Exception as e:
        print(f"Error al obtener corpus de la unidad: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
    
    finally:
        cursor.close()
        conn.close()


from fastapi import Query

#############################################################################3
# Ruta para obtener la respuesta de una actividad específica por usuario y actividad
@app.get("/respuestas/{usuario_id}/{actividad_id}")
async def obtener_respuestas(usuario_id: int, actividad_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        # Consulta para obtener todas las respuestas del usuario a la actividad específica, ordenadas por fecha (más reciente primero)
        cursor.execute("SELECT id, archivo, feedback, fecha FROM respuesta WHERE id_usuario = %s AND id_actividad = %s ORDER BY fecha DESC",
            (usuario_id, actividad_id))
        respuestas = cursor.fetchall()

        cursor.close()
        conn.close()

        if respuestas:
            # Devolver todas las respuestas en una lista
            return [
                {
                    "respuesta_id": respuesta[0],
                    "archivo": respuesta[1],
                    "feedback": respuesta[2],
                    "fecha": respuesta[3]
                }
                for respuesta in respuestas
            ]
        else:
            return {"message": "No se encontraron respuestas para esta actividad y usuario"}

    except Exception as e:
        print(f"Error al obtener las respuestas: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
########################################################################################

@app.post("/upload-and-create-assistant/{unidadId}")
async def upload_and_create_assistant(
    unidadId: int, 
    files: List[UploadFile] = File(...)  # Cambia None por ... para requerir archivos
):
    corpus_ids = []

    conn = connect_db()
    if conn is None:
        raise HTTPException(status_code=500, detail="Error al conectar con la base de datos")

    cursor = conn.cursor()
    
    try:
        # Obtener vector y assistant de la unidad
        cursor.execute("SELECT vector_id, assistant_id FROM unidad WHERE id = %s", (unidadId,))
        result = cursor.fetchone()
        if result is None:
            raise HTTPException(status_code=404, detail="Unidad no encontrada")

        vector_store_id, assistant_id = result

        print(f"🔄 Procesando {len(files)} archivos para vector store: {vector_store_id}")
        
        # ✅ VERIFICAR SI HAY ARCHIVOS EXISTENTES ANTES DE LIMPIAR
        files_existentes = client.beta.vector_stores.files.list(vector_store_id=vector_store_id)
        
        if len(files_existentes.data) > 0:
            print(f"🗑️ Limpiando {len(files_existentes.data)} archivos existentes...")
            await limpiar_vector_store(vector_store_id)
        else:
            print("✅ Vector store ya está vacío, continuando...")
        # Verificar estado antes de empezar
        await verificar_estado_vector_store(vector_store_id)

        for file in files:
            print(f"📤 Subiendo archivo: {file.filename}")
            print(f"🔍 DEBUG - Tipo: {type(file)}, Content-type: {file.content_type}")

            result = await subir_corpus(assistant_id, file, vector_store_id)
            file_id = result["file_id"]
            batch_id = result["batch_id"]

            print(f"✅ Archivo {file.filename} subido exitosamente. File ID: {file_id}")

            # Insertar el corpus en la base de datos
            cursor.execute(
                """
                INSERT INTO corpus (titulo, material, id_unidad)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (file.filename, file_id, unidadId)
            )
            corpus_id = cursor.fetchone()["id"]
            corpus_ids.append(corpus_id)

            conn.commit()

            # Actualizar la unidad con el último batch
            cursor.execute(
                "UPDATE unidad SET batch_id = %s WHERE id = %s",
                (batch_id, unidadId)
            )
            conn.commit()

        # Verificar estado final
        await verificar_estado_vector_store(vector_store_id)


    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error al insertar en la base de datos: {e}")
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error procesando archivos: {e}")
    finally:
        cursor.close()
        conn.close()

    return {
        "message": "Corpus agregado exitosamente", 
        "corpus_id_list": corpus_ids,
        "total_archivos": len(files)
    }



####################################
@app.post("/agregarrespuesta/")
async def agregar_respuesta(
    usuario_id: int = Form(...),
    actividad_id: int = Form(...),
    unidad_id: int = Form(...),
    file: UploadFile = File(...)
):
    try:
        # Conectar a la base de datos 
        conn = connect_db()
        if conn is None:
            raise HTTPException(status_code=500, detail="Error al conectar con la base de datos")
        
        cursor = conn.cursor()

        # Recuperar el assistant_id usando el unidad_id
        cursor.execute("SELECT assistant_id FROM unidad WHERE id = %s", (unidad_id,))
        result = cursor.fetchone()
        if result is None:
            raise HTTPException(status_code=404, detail="Unidad no encontrada")
        
        assistant_id = result[0]

        # Obtener la descripción de la actividad como prompt base
        cursor.execute("SELECT requerimientos FROM actividad WHERE id = %s", (actividad_id,))
        actividad_info = cursor.fetchone()
        if actividad_info is None:
            raise HTTPException(status_code=404, detail="Actividad no encontrada")
        
        # Desempaquetar el resultado de la consulta
        requerimientos_json = actividad_info[0]
        
        # Procesar el archivo usando la función que convierte el archivo a texto
        desarrollo = await procesar_archivo_tar(file)
        
        url = f"{url_api}/Feedback/{assistant_id}/"
        data = {
            "requerimientos":  requerimientos_json,
            "desarrollo": desarrollo,
        }

        response = requests.post(url, data=data)
        respuesta_texto = response.text

        fecha_actual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        try:
            cursor.execute(
                """
                INSERT INTO respuesta (archivo, feedback, id_usuario, id_actividad, fecha)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (file.filename, respuesta_texto, usuario_id, actividad_id, fecha_actual)
            )

            respuesta_id = cursor.fetchone()["id"]
            conn.commit()

        except Exception as e:
            print("Error al insertar en Postgres", e)
            conn.rollback()
            raise HTTPException(status_code=500, detail="Error al insertar en la base de datos")

        finally:
            try:
                cursor.close()
                conn.close()
            except:
                pass

    except Exception as e:
        print(f"Error processing file or getting feedback: {e}")
        raise HTTPException(status_code=500, detail="Error procesando el archivo o obteniendo el feedback")
    
    return {"message": "Respuesta agregada exitosamente", "feedback": respuesta_texto}
########################################################33

################################################
# Ruta para eliminar un corpus
@app.delete("/corpus/{corpus_id}")
async def eliminar_corpus(corpus_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()
        cursor.execute("SELECT material FROM corpus WHERE id = %s", (corpus_id,))
        corpus = cursor.fetchone()
        if not corpus:
            raise HTTPException(status_code=404, detail="Corpus no encontrado")
        # Imprimir el tipo y contenido de 'corpus' para depuración
        print("Tipo de 'corpus':", type(corpus))
        print("Contenido de 'corpus':", corpus)
        file_id = corpus[0]  # Asumiendo que 'material' almacena el file_id
        await eliminar_archivo(file_id)
        # Eliminar el corpus de la tabla corpus
        cursor.execute("DELETE FROM corpus WHERE id = %s", (corpus_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return JSONResponse(status_code=200, content={"message": "Corpus eliminado correctamente"})

    except Exception as e:
        print(f"Error al eliminar el corpus: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
##################################################################3

#################################################################
@app.put("/corpus/{corpus_id}")
async def reemplazar_corpus(corpus_id: int, request: Request, file: UploadFile = File(...)):
    try:
        form_data = await request.form()
        unidadId = form_data.get('unidadId')

        conn = connect_db()
        cursor = conn.cursor()

        # Obtener file_id actual
        cursor.execute("SELECT material FROM corpus WHERE id = %s", (corpus_id,))
        corpus = cursor.fetchone()
        if not corpus:
            raise HTTPException(status_code=404, detail="Corpus no encontrado")

        # Obtener vector y assistant de unidad
        cursor.execute("SELECT vector_id, assistant_id FROM unidad WHERE id = %s", (unidadId,))
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Unidad no encontrada")

        vector_store_id, assistant_id = result

        # Eliminar archivo anterior
        await eliminar_archivo(corpus[0])

        # Subir el nuevo
        archivo_contenido = await file.read()
        archivo_subido = (file.filename, archivo_contenido)
        result = await subir_corpus(assistant_id, archivo_subido, vector_store_id)
        nuevo_file_id = result["file_id"]
        batch_id = result["batch_id"]

        # Actualizar corpus
        cursor.execute(
            "UPDATE corpus SET titulo = %s, material = %s WHERE id = %s",
            (file.filename, nuevo_file_id, corpus_id)
        )
        conn.commit()

        # Actualizar batch en unidad
        cursor.execute(
            "UPDATE unidad SET batch_id = %s WHERE id = %s",
            (batch_id, unidadId)
        )
        conn.commit()

        cursor.close()
        conn.close()

        return JSONResponse(status_code=200, content={"message": "Corpus reemplazado correctamente", "nuevo_file_id": nuevo_file_id})

    except Exception as e:
        print(f"Error al reemplazar el corpus: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

#################################333



########################################
# Ruta para eliminar el perfil del usuario
@app.delete("/perfil/{user_id}")
async def delete_profile(user_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        # Consulta para eliminar el usuario de la base de datos
        delete_query = "DELETE FROM usuario WHERE id = %s"
        cursor.execute(delete_query, (user_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return {"message": "Usuario eliminado exitosamente"}

    except Exception as e:
        print(f"Error al eliminar usuario: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
#########################################


############################################################
@app.put("/respuestas/{respuesta_id}")
async def actualizar_respuesta(respuesta_id: int, request: Request):
    # Imprimir el ID de la respuesta y datos iniciales
    print(f"Recibiendo petición para actualizar la respuesta con ID: {respuesta_id}")
    
    data = await request.json()
    nuevo_feedback = data.get('feedback')
    
    # Verificar si se ha proporcionado feedback
    if not nuevo_feedback:
        raise HTTPException(status_code=400, detail="Se requiere feedback para actualizar la respuesta")
    
    print(f"Nuevo feedback recibido: {nuevo_feedback}")

    try:
        conn = connect_db()
        cursor = conn.cursor()

        # Actualizar la respuesta en la base de datos
        print(f"Actualizando respuesta en la base de datos para respuesta_id: {respuesta_id}")
        cursor.execute("UPDATE respuesta SET feedback = %s WHERE id = %s", (nuevo_feedback, respuesta_id))
        conn.commit()

        # Obtener información adicional para la notificación
        cursor.execute("""
            SELECT r.id_usuario, u.id_curso, a.titulo, a.id 
            FROM respuesta r 
            JOIN actividad a ON r.id_actividad = a.id
            JOIN unidad u ON a.id_unidad = u.id
            WHERE r.id = %s
        """, (respuesta_id,))
        result = cursor.fetchone()

        # Verificar el resultado de la consulta
        if result:
            id_usuario = result[0]
            id_curso = result[1]
            actividad_nombre = result[2]
            actividad_id = result[3]  # Aquí se obtiene el ID de la actividad

            print(f"ID del usuario: {id_usuario}, ID del curso: {id_curso}, Nombre de la actividad: {actividad_nombre}, ID de la actividad: {actividad_id}")

            # Insertar notificación en la tabla notificacion
            fecha_actual = datetime.now().date()
            hora_actual = datetime.now().time()
            titulo = "Actualización de feedback"
            comentario = f"El feedback de su respuesta en la actividad '{actividad_nombre}' ha sido actualizado"
            leido = 0

            print(f"Insertando notificación para el usuario {id_usuario} en el curso {id_curso} para la actividad {actividad_nombre}")

            cursor.execute("""
                INSERT INTO notificacion (titulo, comentario, fecha, hora, leido, id_usuario, id_curso, id_actividad, id_respuesta)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (titulo, comentario, fecha_actual, hora_actual, leido, id_usuario, id_curso, actividad_id, respuesta_id))
            conn.commit()  # Esto es crucial para aplicar los cambios en la base de datos.
    except Exception as e:
        print(f"Error al actualizar la respuesta o insertar la notificación: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

    finally:
        print(f"Cerrando la conexión a la base de datos para respuesta_id: {respuesta_id}")
        cursor.close()
        conn.close()

    print(f"Respuesta y notificación actualizadas exitosamente para respuesta_id: {respuesta_id}")
    return {"message": "Respuesta y notificación actualizadas exitosamente"}


############################################################# 
'''
    assistant = await create_assistant(
        descripcion="Eres un tutor personal de código. Ayudas a los estudiantes a comprender y resolver problemas de programación.",
        instruccions=(
            "Interpreta el código que el alumno te ha proporcionado y entrégale feedback sobre cómo mejorar su solución. "
            "La solución responde a la actividad que contiene el archivo proporcionado. El feedback debe ser claro y conciso. "
            "El feedback recalca los errores más importantes y sugiere formas de mejorar la solución."
        )
    )

    file_id = await upload_file_to_assistant(file, assistant.id)
    
    # Guardar el file_id temporalmente (aquí solo se imprime, puedes adaptarlo según necesites)
    print("File ID:", file_id)

    return {"message": "Assistant created and file uploaded successfully", "file_id": file_id}
'''

async def actualizar_estados_periodicamente():
    while True:
        now = datetime.now()
        try:
            conn = connect_db()
            cursor = conn.cursor()

            # Actualizar estados a 1 para actividades que están pendientes (estado 2) y cuya fecha y hora han llegado
            cursor.execute("""
                UPDATE actividad
                SET estado = 1,
                    fecha_inicio = NULL,
                    hora_inicio = NULL
                WHERE fecha_inicio <= %s AND hora_inicio <= %s AND estado = 2
            """, (now.date(), now.time()))

            # Actualizar estados a 3 para actividades que están activas (estado 1) y cuya fecha y hora de cierre han llegado
            cursor.execute("""
                UPDATE actividad
                SET estado = 3,
                    fecha_cierre = NULL,
                    hora_cierre = NULL
                WHERE fecha_cierre <= %s AND hora_cierre <= %s AND estado = 1
            """, (now.date(), now.time()))

            conn.commit()
            cursor.close()
            conn.close()

        except Exception as e:
            print(f"Error al actualizar estados: {e}")

        # Espera 60 segundos antes de ejecutar nuevamente
        await asyncio.sleep(60)

#@app.on_event("startup")
#async def startup_event():
#    asyncio.create_task(actualizar_estados_periodicamente())

@app.get("/")
async def root():
    return {"message": "Servidor en ejecución"}

@app.get("/unidad/{unidad_id}/evaluaciones")
async def get_evaluaciones_por_unidad(unidad_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        
        # Consulta para obtener las evaluaciones de la unidad especificada
        cursor.execute("""
            SELECT id, titulo, descripcion, preguntas_desarrollo, preguntas_alternativas, preguntas_vf, puntaje_total, versiones 
            FROM evaluacion WHERE id_unidad = %s
        """, (unidad_id,))
        evaluaciones = cursor.fetchall()
        
        for evaluacion in evaluaciones:
            evaluacion_id = evaluacion['id']
            
            # Obtener preguntas de desarrollo
            cursor.execute("SELECT id, enunciado, respuesta, puntaje FROM desarrollo WHERE id_evaluacion = %s", (evaluacion_id,))
            preguntas_desarrollo = cursor.fetchall()
            evaluacion['preguntas_desarrollo'] = preguntas_desarrollo
            
            # Obtener preguntas de alternativas
            cursor.execute("SELECT id, enunciado, respuesta_a, respuesta_b, respuesta_c, respuesta_d, respuesta_e, correcta, puntaje FROM alternativas WHERE id_evaluacion = %s", (evaluacion_id,))
            preguntas_alternativas = cursor.fetchall()
            evaluacion['preguntas_alternativas'] = preguntas_alternativas
            
            # Obtener preguntas de verdadero/falso
            cursor.execute("SELECT id, enunciado, correcta, puntaje FROM vf WHERE id_evaluacion = %s", (evaluacion_id,))
            preguntas_vf = cursor.fetchall()
            evaluacion['preguntas_vf'] = preguntas_vf

        cursor.close()
        conn.close()
        
        # Verificar si se encontraron evaluaciones
        if not evaluaciones:
            return {"message": "No se encontraron evaluaciones para la unidad especificada"}
        
        # Retornar las evaluaciones con sus preguntas como JSON
        return {"evaluaciones": evaluaciones}
    
    except Exception as e:
        print(f"Error al obtener evaluaciones por unidad: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")


@app.post("/unidad/{unidad_id}/evaluacion")
async def crear_evaluacion(unidad_id: int, request: Request):
    data = await request.json()
    titulo = data.get('titulo')
    descripcion = data.get('descripcion')
    preguntas_desarrollo = data.get('preguntas_desarrollo', '0') 
    preguntas_alternativas = data.get('preguntas_alternativas', '0')
    preguntas_vf = data.get('preguntas_vf', '0')
    puntaje_total = data.get('puntaje') 
    curso_id = data.get('curso_id')  # Nueva línea para obtener el campo curso_id
    versiones = data.get('versiones')  # Nueva línea para obtener el campo versiones
    dificultad = data.get('dificultad')  # Nueva línea para obtener el campo dificultad

    print(unidad_id, curso_id)

    print(f"Datos recibidos: {titulo}, {descripcion}, {preguntas_desarrollo}, {preguntas_alternativas}, {preguntas_vf}, {puntaje_total}, {versiones}, {dificultad}")
    if not titulo or not descripcion:
        raise HTTPException(status_code=400, detail="Se requieren tanto título como descripción para crear una evaluación")
    if not (1 <= int(versiones) <= 10):
        raise HTTPException(status_code=400, detail="El número de versiones debe estar entre 1 y 10")

    print("hola")
    #--------------------
    
    # Determinar el assistant_id basado en el curso_id
    if curso_id in ['1', 1] and unidad_id in ['1', 1]:
        assistant_id = 'asst_pCKuTpobSzVJFnLQ5IBTrMb5'
    else:
        # Conectar a la base de datos para obtener el assistant_id
        conn = connect_db()
        
        if conn is None:
            raise HTTPException(status_code=500, detail="Error al conectar a la base de datos")
        cursor = conn.cursor()
        print(conn, cursor)
        try:
            print("hola popos ")
            cursor.execute("SELECT assistant_id FROM unidad WHERE id = %s", (unidad_id,))
            result = cursor.fetchone()
            print(f"Resultado de la consulta: {result}")
            if result:
                assistant_id = result[0]
            else:
                raise HTTPException(status_code=404, detail="Curso no encontrado")
        except Exception as e:
            print("es la coneccion culia")
            raise HTTPException(status_code=500, detail=f"Error al consultar la base de datos: {e}")
        finally:
            cursor.close()
            conn.close()
    #--------------------
    try:
        conn = connect_db()
        cursor = conn.cursor()

        # Insertar la evaluación
        cursor.execute(
            """
            INSERT INTO evaluacion (
                titulo, descripcion, id_unidad,
                preguntas_desarrollo, preguntas_alternativas, preguntas_vf,
                puntaje_total, versiones, dificultad
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                titulo, descripcion, unidad_id,
                preguntas_desarrollo, preguntas_alternativas, preguntas_vf,
                puntaje_total, versiones, dificultad
            )
        )

        evaluacion_id = cursor.fetchone()["id"]

        # Llamada a la API para crear-preguntas
        url = f"{url_api}/crear-preguntas/{assistant_id}"
        params = {
            'vf': preguntas_vf,
            'desarrollo': preguntas_desarrollo,
            'alternativas': preguntas_alternativas,
            'dificultad': dificultad
        }
        response = requests.post(url, params=params)


        if response.status_code == 200:
            preguntas, thread_id = response.json()  # Ajuste aquí para capturar thread_id
            # Insertar las preguntas en la base de datos
            print("PREGUNTAS\n" + preguntas)
            generar_e_insertar_preguntas_por_tipo(cursor, preguntas, evaluacion_id, puntaje=None)

            # Actualizar la evaluación para incluir el thread ID
            cursor.execute("""
                UPDATE evaluacion SET thread = %s WHERE id = %s
            """, (thread_id, evaluacion_id))

        else:
            print(f"Error {response.status_code}: {response.text}")
            raise HTTPException(status_code=response.status_code, detail=response.text)
        
        conn.commit()
        cursor.close()
        conn.close()

        return JSONResponse(status_code=201, content={"message": "Evaluación creada exitosamente", "evaluacion_id": evaluacion_id})

    except Exception as e:
        print(f"Error al crear la evaluación: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
    
# Ruta para eliminar una evaluacion
@app.delete("/evaluacion/{evaluacion_id}")
async def eliminar_evaluacion(evaluacion_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        # Eliminar la evaluacion de la base de datos
        cursor.execute("DELETE FROM evaluacion WHERE id = %s", (evaluacion_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return JSONResponse(status_code=200, content={"message": "Evaluacion eliminada correctamente"})

    except Exception as e:
        print(f"Error al eliminar la evaluacion: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

@app.get("/evaluacion/{evaluacion_id}/preguntas")
async def get_evaluacion_preguntas(evaluacion_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()


        # Obtener título, descripción y versiones de la evaluación
        cursor.execute("""
            SELECT titulo, descripcion, versiones, thread
            FROM evaluacion
            WHERE id = %s
        """, (evaluacion_id,))
        evaluacion = cursor.fetchone()

        if not evaluacion:
            raise HTTPException(status_code=404, detail="Evaluación no encontrada")

        # Obtener preguntas alternativas
        cursor.execute("""
            SELECT 'alternativa' AS tipo, id, enunciado, respuesta_a, respuesta_b, respuesta_c, 
                respuesta_d, respuesta_e, correcta, puntaje
            FROM alternativas
            WHERE id_evaluacion = %s
        """, (evaluacion_id,))
        alternativas = cursor.fetchall()

        # Obtener preguntas de verdadero/falso
        cursor.execute("""
            SELECT 'vf' AS tipo, id, enunciado, correcta, puntaje
            FROM vf
            WHERE id_evaluacion = %s
        """, (evaluacion_id,))
        vf_preguntas = cursor.fetchall()

        # Obtener preguntas de desarrollo
        cursor.execute("""
            SELECT 'desarrollo' AS tipo, id, enunciado, respuesta, puntaje
            FROM desarrollo
            WHERE id_evaluacion = %s
        """, (evaluacion_id,))
        desarrollo_preguntas = cursor.fetchall()

        # Agrupar todas las preguntas y la información de la evaluación
        resultado = {
            "evaluacion": {
                "titulo": evaluacion["titulo"],
                "descripcion": evaluacion["descripcion"],
                "versiones": str(evaluacion["versiones"]) if evaluacion["versiones"] else "" , # Convertir a string vacío si versiones es None o 0
                "thread": evaluacion["thread"],  # Ajustar aquí para incluir el thread ID         
            },
            "preguntas": {
                "alternativas": alternativas,
                "vf": vf_preguntas,
                "desarrollo": desarrollo_preguntas
            }
        }

        cursor.close()
        conn.close()

        return resultado

    except Exception as e:
        print(f"Error al obtener preguntas de la evaluación: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")


# Ruta para eliminar una pregunta de alternativas
@app.delete("/pregunta/alternativa/{pregunta_id}")
async def eliminar_pregunta_alternativa(pregunta_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM alternativas WHERE id = %s", (pregunta_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return JSONResponse(status_code=200, content={"message": "Pregunta de alternativas eliminada correctamente"})
    except Exception as e:
        print(f"Error al eliminar pregunta de alternativas: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

# Ruta para eliminar una pregunta de Verdadero/Falso
@app.delete("/pregunta/vf/{pregunta_id}")
async def eliminar_pregunta_vf(pregunta_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM vf WHERE id = %s", (pregunta_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return JSONResponse(status_code=200, content={"message": "Pregunta de Verdadero/Falso eliminada correctamente"})
    except Exception as e:
        print(f"Error al eliminar pregunta de Verdadero/Falso: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

# Ruta para eliminar una pregunta de desarrollo
@app.delete("/pregunta/desarrollo/{pregunta_id}")
async def eliminar_pregunta_desarrollo(pregunta_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM desarrollo WHERE id = %s", (pregunta_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return JSONResponse(status_code=200, content={"message": "Pregunta de desarrollo eliminada correctamente"})
    except Exception as e:
        print(f"Error al eliminar pregunta de desarrollo: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

@app.put("/alternativa/{pregunta_id}")
async def actualizar_alternativa(pregunta_id: int, pregunta: dict):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE alternativas SET enunciado = %s, respuesta_a = %s, respuesta_b = %s,
            respuesta_c = %s, respuesta_d = %s, respuesta_e = %s, correcta = %s, puntaje = %s
            WHERE id = %s
        """, (
            pregunta['enunciado'], pregunta['respuesta_a'], pregunta['respuesta_b'], pregunta['respuesta_c'],
            pregunta['respuesta_d'], pregunta['respuesta_e'], pregunta['correcta'], pregunta['puntaje'], pregunta_id
        ))

        conn.commit()
        cursor.close()
        conn.close()

        return JSONResponse(status_code=200, content={"message": "Pregunta alternativa actualizada correctamente"})

    except Exception as e:
        print(f"Error al actualizar pregunta alternativa: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

@app.put("/vf/{pregunta_id}")
async def actualizar_vf(pregunta_id: int, pregunta: dict):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE vf SET enunciado = %s, correcta = %s, puntaje = %s
            WHERE id = %s
        """, (pregunta['enunciado'], pregunta['correcta'], pregunta['puntaje'], pregunta_id))

        conn.commit()
        cursor.close()
        conn.close()

        return JSONResponse(status_code=200, content={"message": "Pregunta de verdadero/falso actualizada correctamente"})

    except Exception as e:
        print(f"Error al actualizar pregunta de verdadero/falso: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
@app.put("/desarrollo/{pregunta_id}")
async def actualizar_desarrollo(pregunta_id: int, pregunta: dict):
    print(f"Datos de pregunta: {pregunta}")
    try:
        conn = connect_db()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE desarrollo SET enunciado = %s, respuesta = %s, puntaje = %s
            WHERE id = %s
        """, (pregunta['enunciado'], pregunta['respuesta'], pregunta['puntaje'], pregunta_id))

        conn.commit()
        cursor.close()
        conn.close()

        return JSONResponse(status_code=200, content={"message": "Pregunta de desarrollo actualizada correctamente"})

    except Exception as e:
        print(f"Error al actualizar pregunta de desarrollo: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")



@app.get("/unidad/{unidad_id}/foro")
async def get_foro_unidad(unidad_id: int):
    try:
        conn = connect_db()  # Asegúrate de definir esta función en tu código
        cursor = conn.cursor()


        # Consulta para obtener el ID del curso desde la unidad
        cursor.execute("""
            SELECT id_curso 
            FROM unidad 
            WHERE id = %s
        """, (unidad_id,))
        unidad = cursor.fetchone()
        
        # Si no se encuentra la unidad, devolver un error
        if not unidad:
            raise HTTPException(status_code=404, detail="Unidad no encontrada")
        
        curso_id = unidad['id_curso']
        
        # Consulta para obtener los foros de la unidad junto con el nombre y tipo de usuario
        cursor.execute(""" 
            SELECT foro.id, foro.titulo, foro.descripcion, foro.fecha, foro.hora, 
                foro.id_usuario,  -- Asegúrate de seleccionar el id_usuario
                usuario.nombre AS nombre_usuario, 
                usuario.tipo AS tipo_usuario
            FROM foro
            JOIN usuario ON foro.id_usuario = usuario.id
            WHERE foro.id_unidad = %s
            ORDER BY foro.fecha DESC, foro.hora DESC  -- Ordenar por fecha y hora más recientes
        """, (unidad_id,))

        foro = cursor.fetchall()

        # Transformar la hora en formato "HH:MM:SS" si es timedelta
        for discusion in foro:
            if isinstance(discusion['hora'], timedelta):
                seconds = discusion['hora'].total_seconds()
                hours = int(seconds // 3600)
                minutes = int((seconds % 3600) // 60)
                seconds = int(seconds % 60)
                discusion['hora'] = f"{hours:02}:{minutes:02}:{seconds:02}"

        # Retornar los foros y el curso_id, aunque foros esté vacío
        return {"foro": foro, "curso_id": curso_id}
    
    except Exception as e:
        print(f"Error al obtener foro de la unidad: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
    
    finally:
        cursor.close()
        conn.close()



@app.post("/regenerar-pregunta/{pregunta_tipo}")
async def regenerar_pregunta(pregunta_tipo: str, request: Request):
    try:
        data = await request.json()
        thread = data.get("thread")  # Obtener el thread del cuerpo de la solicitud
        unidad_id = data.get('unidad_id')  # Obtener el unidad_id
        evaluacion_id = data.get('evaluacion_id')  # Obtener el ID de la evaluación
        pregunta_id = data.get('pregunta_id')
        
        print(f"Datos recibidos: {pregunta_tipo}, {thread}, {unidad_id}, {evaluacion_id}")
        # Verifica si el curso_id está dentro de los valores específicos para asignar un assistant_id
        if unidad_id in ['1', 1]:
            assistant_id = 'asst_pCKuTpobSzVJFnLQ5IBTrMb5'
        else:
            # Conectar a la base de datos para obtener el assistant_id
            conn = connect_db()
            if conn is None:
                raise HTTPException(status_code=500, detail="Error al conectar a la base de datos")
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT assistant_id FROM unidad WHERE id = %s", (unidad_id,))
                result = cursor.fetchone()
                if result:
                    assistant_id = result[0]
                else:
                    raise HTTPException(status_code=404, detail="Curso no encontrado")
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error al consultar la base de datos: {e}")
            finally:
                cursor.close()
                conn.close()  # Cerrar conexión

        # Llamada a la API para regenerar-preguntas
        url = f"{url_api}/regenerar-preguntas/{assistant_id}"
        params = {
            'assistant_id': assistant_id,
            'thread_id': thread,  # Aquí incluimos el thread en los parámetros
            'pregunta_tipo': pregunta_tipo,
        }
        response = requests.post(url, params=params)

        if response.status_code == 200:
            preguntas = response.json()

            conn = connect_db()
            cursor = conn.cursor()
            
            # Verificar el tipo de pregunta y llamar a la función adecuada
            if pregunta_tipo == 'desarrollo':
                pregunta_nueva = parse_desarrollo(preguntas)
                insertar_desarrollo(cursor, pregunta_nueva, evaluacion_id,pregunta_id)
            elif pregunta_tipo == 'alternativa':
                pregunta_nueva = parse_alternativas(preguntas)
                insertar_alternativa(cursor, pregunta_nueva, evaluacion_id,pregunta_id)
            elif pregunta_tipo == 'vf':
                pregunta_nueva = parse_vf(preguntas)
                insertar_vf(cursor, pregunta_nueva, evaluacion_id,pregunta_id)
            else:
                raise HTTPException(status_code=400, detail="Tipo de pregunta no válido")

            conn.commit()
            cursor.close()
            conn.close()

            # Devolver la nueva pregunta generada
            print(f"Pregunta DEVUELTA: {preguntas}")
            return {"nueva_pregunta": preguntas}
        else:
            raise HTTPException(status_code=response.status_code, detail=response.text)

    except Exception as e:
        print(f"Error al regenerar pregunta: {e}")
        raise HTTPException(status_code=500, detail=f"Error interno del servidor: {e}")



#crear un foro 
@app.post("/unidad/{unidad_id}/foro")
async def crear_foro(unidad_id: int, request: Request):
    try:
        # Obtener los datos del cuerpo de la solicitud
        data = await request.json()
        titulo = data.get('titulo')
        descripcion = data.get('descripcion')
        id_usuario = data.get('id_usuario')
        
        # Validar que los campos requeridos estén presentes
        if not titulo or not descripcion or not id_usuario:
            raise HTTPException(status_code=400, detail="Se requieren título, descripción e id_usuario para crear el foro")
        
        # Obtener la fecha y hora actuales en local
        fecha_actual = datetime.now().date()  # Fecha en formato YYYY-MM-DD
        hora_actual = datetime.now().time()  # Hora en formato HH:MM:SS

        # Conectar a la base de datos
        conn = connect_db()
        cursor = conn.cursor()

        # Insertar el nuevo foro
        cursor.execute(
            """
            INSERT INTO foro (titulo, descripcion, fecha, hora, id_usuario, id_unidad)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (titulo, descripcion, fecha_actual, hora_actual, id_usuario, unidad_id)
        )

        foro_id = cursor.fetchone()["id"]

        # Obtener el ID del curso al que pertenece la unidad
        cursor.execute(
            "SELECT id_curso FROM unidad WHERE id = %s",
            (unidad_id,)
        )
        curso_result = cursor.fetchone()
        if not curso_result:
            raise HTTPException(status_code=404, detail="Unidad no encontrada")


        id_curso = curso_result[0]

        # Obtener a todos los participantes del curso
        cursor.execute("""
            SELECT id_usuario FROM usuario_curso WHERE id_curso = %s
        """, (id_curso,))
        participantes = cursor.fetchall()

        # Insertar notificación para todos los participantes excepto el creador del foro
        for (id_participante,) in participantes:
            if id_participante != id_usuario:  # No enviar notificación a sí mismo
                cursor.execute("""
                    INSERT INTO notificacion (titulo, comentario, fecha, hora, leido, id_usuario, id_curso, id_actividad, id_respuesta)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    "Nuevo foro creado",
                    f"Se ha creado el foro '{titulo}'",
                    fecha_actual,
                    hora_actual,
                    0,  # No leído
                    id_participante,
                    id_curso,
                    None,
                    None
                ))

        # Confirmar la transacción
        conn.commit()

        # Cerrar el cursor y la conexión
        cursor.close()
        conn.close()

        # Devolver una respuesta con el ID del foro recién creado
        return JSONResponse(status_code=201, content={"message": "Foro creado exitosamente", "foro_id": foro_id})

    except Exception as e:
        print(f"Error al crear el foro: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
    
# Ruta para eliminar un foro
@app.delete("/foro/{foro_id}")
async def eliminar_foro(foro_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        # Eliminar el foro de la base de datos
        cursor.execute("DELETE FROM foro WHERE id = %s", (foro_id,))

        # Verificar cuántas filas se eliminaron
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Foro no encontrado")

        conn.commit()
        
        return JSONResponse(status_code=200, content={"message": "Foro eliminado correctamente"})

    except Exception as e:
        print(f"Error al eliminar el foro: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

    finally:
        cursor.close()
        conn.close()

@app.get("/foro/{foro_id}/respuestas")
async def get_respuestas_foro(foro_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()


        # Consulta para obtener las respuestas del foro
        cursor.execute("""
            SELECT respuesta_foro.id, respuesta_foro.comentario, 
                   respuesta_foro.fecha, respuesta_foro.hora,
                   respuesta_foro.id_usuario, 
                   usuario.nombre AS nombre_usuario,
                   usuario.tipo AS tipo_usuario
            FROM respuesta_foro
            JOIN usuario ON respuesta_foro.id_usuario = usuario.id
            WHERE respuesta_foro.id_foro = %s
            ORDER BY respuesta_foro.fecha DESC, respuesta_foro.hora DESC
        """, (foro_id,))

        respuestas = cursor.fetchall()

        # Transformar la hora en formato "HH:MM:SS" si es timedelta
        for respuesta in respuestas:
            if isinstance(respuesta['hora'], timedelta):
                seconds = respuesta['hora'].total_seconds()
                hours = int(seconds // 3600)
                minutes = int((seconds % 3600) // 60)
                seconds = int(seconds % 60)
                respuesta['hora'] = f"{hours:02}:{minutes:02}:{seconds:02}"

        # Retornar las respuestas (puede estar vacío)
        return {"respuestas": respuestas}

    except Exception as e:
        print(f"Error al obtener respuestas del foro: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

    finally:
        cursor.close()
        conn.close()

@app.post("/foro/{id_foro}/respuesta")
async def crear_respuesta(id_foro: int, request: Request):
    try:
        # Obtener los datos del cuerpo de la solicitud
        data = await request.json()
        print(f"Datos recibidos: {data}")  # Log para depurar

        comentario = data.get('comentario')
        id_usuario = data.get('id_usuario')

        # Validar que los campos requeridos estén presentes
        if not comentario or not id_usuario:
            raise HTTPException(status_code=400, detail="Se requieren comentario e id_usuario para crear la respuesta")
        
        # Obtener la fecha y hora actuales
        fecha_actual = datetime.now().date()
        hora_actual = datetime.now().time()

        conn = connect_db()
        cursor = conn.cursor()

        # Insertar la nueva respuesta en la tabla respuesta_foro
        cursor.execute(
            """
            INSERT INTO respuesta_foro (comentario, fecha, hora, id_usuario, id_foro)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (comentario, fecha_actual, hora_actual, id_usuario, id_foro)
        )

        respuesta_id = cursor.fetchone()["id"]

        # Obtener la información del foro y su curso asociado
        cursor.execute(
            """
            SELECT f.titulo, f.id_usuario, u.id_curso
            FROM foro f
            JOIN unidad u ON f.id_unidad = u.id
            WHERE f.id = %s
            """,
            (id_foro,)
        )
        foro_result = cursor.fetchone()

        if foro_result:
            foro_titulo = foro_result[0]
            id_usuario_creador_foro = foro_result[1]
            id_curso = foro_result[2]

            # Obtener a todos los participantes del curso
            cursor.execute("""
                SELECT id_usuario FROM usuario_curso WHERE id_curso = %s
            """, (id_curso,))
            participantes = cursor.fetchall()

            # Insertar notificación para todos los participantes excepto el creador de la respuesta
            for (id_participante,) in participantes:
                if id_participante != id_usuario:  # No enviar notificación a sí mismo
                    cursor.execute("""
                        INSERT INTO notificacion (titulo, comentario, fecha, hora, leido, id_usuario, id_curso, id_actividad, id_respuesta)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        "Nuevo comentario en el foro",
                        f"Un usuario ha comentado en el foro '{foro_titulo}'",
                        fecha_actual,
                        hora_actual,
                        0,  # No leído
                        id_participante,
                        id_curso,
                        None,
                        None
                    ))

        # Confirmar la transacción
        conn.commit()

        # Cerrar el cursor y la conexión
        cursor.close()
        conn.close()

        # Devolver una respuesta con el ID de la respuesta recién creada
        return JSONResponse(status_code=201, content={"message": "Respuesta y notificación creadas exitosamente", "respuesta_id": respuesta_id})

    except Exception as e:
        print(f"Error al crear la respuesta o la notificación: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
@app.get("/notificaciones/{user_id}")
async def get_notificaciones(user_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()


        # Consulta para obtener las notificaciones del usuario
        cursor.execute("""
            SELECT notificacion.id, notificacion.titulo, notificacion.comentario, 
                   notificacion.fecha, notificacion.hora, notificacion.leido,
                   notificacion.id_actividad, actividad.titulo AS actividad_titulo
            FROM notificacion
            LEFT JOIN actividad ON notificacion.id_actividad = actividad.id
            WHERE notificacion.id_usuario = %s
            ORDER BY notificacion.fecha DESC, notificacion.hora DESC
        """, (user_id,))

        notificaciones = cursor.fetchall()

        # Transformar la hora en formato "HH:MM:SS" si es timedelta
        for notificacion in notificaciones:
            if isinstance(notificacion['hora'], timedelta):
                seconds = notificacion['hora'].total_seconds()
                hours = int(seconds // 3600)
                minutes = int((seconds % 3600) // 60)
                seconds = int(seconds % 60)
                notificacion['hora'] = f"{hours:02}:{minutes:02}:{seconds:02}"

        # Retornar las notificaciones (puede estar vacío)
        return {"notificaciones": notificaciones}

    except Exception as e:
        print(f"Error al obtener notificaciones del usuario: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

    finally:
        cursor.close()
        conn.close()

@app.get("/notificaciones/tiene-no-leidas/{user_id}")
async def tiene_notificaciones_no_leidas(user_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        # Consulta para verificar si hay notificaciones no leídas (leido = 0)
        cursor.execute("""
            SELECT EXISTS (
                SELECT 1 
                FROM notificacion 
                WHERE id_usuario = %s AND leido = 0
            ) AS tiene_no_leidas
        """, (user_id,))

        resultado = cursor.fetchone()

        # Retornar un objeto con la información
        return {"tiene_no_leidas": bool(resultado[0])}

    except Exception as e:
        print(f"Error al comprobar notificaciones no leídas del usuario: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

    finally:
        cursor.close()
        conn.close()

@app.put("/notificaciones/{notificacion_id}")
async def actualizar_notificacion(notificacion_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        # Actualiza el estado de la notificación a leída (leido = 1)
        cursor.execute("""
            UPDATE notificacion SET leido = 1 WHERE id = %s
        """, (notificacion_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return JSONResponse(status_code=200, content={"message": "Notificación actualizada correctamente"})

    except Exception as e:
        print(f"Error al actualizar notificación: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")


def enviar_notificacion(id_usuario, id_curso, id_actividad, comentario):
    conn = connect_db()
    cursor = conn.cursor()
    fecha_actual = datetime.now().date()
    hora_actual = datetime.now().time()

    try:
        cursor.execute("""INSERT INTO notificacion (titulo, comentario, fecha, hora, leido, id_usuario, id_curso, id_actividad, id_respuesta)
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                       ("24 horas para cierre de actividad" if "24" in comentario else "1 hora para cierre de actividad", 
                        comentario, 
                        fecha_actual, 
                        hora_actual, 
                        0,  
                        id_usuario, 
                        id_curso, 
                        id_actividad, 
                        None))

        conn.commit()
        print("Notificación enviada: ", comentario)  # Debugging
    except Exception as e:
        print(f"Ocurrió un error al enviar la notificación: {e}")  # Debugging
    finally:
        cursor.close()
        conn.close()




def verificar_actividades():
    # Conectar a la base de datos
    conn = connect_db()
    cursor = conn.cursor()

    ahora = datetime.now()

    # Rango para actividades que cierran entre 59 minutos y 1 hora
    hora_cierre_inicio_1h = ahora + timedelta(minutes=59)  # 59 minutos a partir de ahora
    hora_cierre_fin_1h = ahora + timedelta(hours=1)  # 1 hora a partir de ahora

    # Imprimir los rangos para depuración
    print(f"Buscando actividades que cierran entre: {hora_cierre_inicio_1h.time()} y {hora_cierre_fin_1h.time()} en la fecha: {ahora.date()}")

    cursor.execute("""
        SELECT a.id, a.titulo, c.id AS id_curso, uc.id_usuario
        FROM actividad a
        JOIN unidad u ON a.id_unidad = u.id
        JOIN curso c ON u.id_curso = c.id
        JOIN usuario_curso uc ON uc.id_curso = c.id
        WHERE a.fecha_cierre = %s AND a.hora_cierre BETWEEN %s AND %s
    """, (ahora.date(), hora_cierre_inicio_1h.time(), hora_cierre_fin_1h.time()))

    actividades_1_hora = cursor.fetchall()
    print(f"Actividades a 1 hora de cierre: {actividades_1_hora}")

    for actividad in actividades_1_hora:
        id_actividad = actividad[0]
        nombre_actividad = actividad[1]
        id_curso = actividad[2]
        id_usuario = actividad[3]

        comentario = f"Queda 1 hora para el cierre de la actividad {nombre_actividad}."
        enviar_notificacion(id_usuario, id_curso, id_actividad, comentario)

    # Rango para actividades que cierran entre ahora y 1 minuto antes en 24 horas
    hora_cierre_inicio_24h = ahora - timedelta(minutes=1)  # Hora actual menos 1 minuto
    hora_cierre_fin_24h = ahora + timedelta(hours=24)  # Hora actual más 24 horas

    # Imprimir los rangos para depuración
    print(f"Buscando actividades que cierran entre: {hora_cierre_inicio_24h.time()} y {hora_cierre_fin_24h.time()} en la fecha: {(ahora + timedelta(days=1)).date()}")

    cursor.execute("""
        SELECT a.id, a.titulo, c.id AS id_curso, uc.id_usuario
        FROM actividad a
        JOIN unidad u ON a.id_unidad = u.id
        JOIN curso c ON u.id_curso = c.id
        JOIN usuario_curso uc ON uc.id_curso = c.id
        WHERE (a.fecha_cierre = %s AND a.hora_cierre BETWEEN %s AND %s)
        OR (a.fecha_cierre = %s AND a.hora_cierre BETWEEN %s AND %s)
    """, (
        ahora.date(), hora_cierre_inicio_24h.time(), hora_cierre_fin_24h.time(),  # Día actual
        (ahora + timedelta(days=1)).date(), hora_cierre_inicio_24h.time(), hora_cierre_fin_24h.time()  # Día siguiente
    ))

    actividades_24_horas = cursor.fetchall()
    print(f"Actividades a 24 horas de cierre: {actividades_24_horas}")

    for actividad in actividades_24_horas:
        id_actividad = actividad[0]
        nombre_actividad = actividad[1]
        id_curso = actividad[2]
        id_usuario = actividad[3]

        comentario = f"Quedan 24 horas para el cierre de la actividad {nombre_actividad}."
        enviar_notificacion(id_usuario, id_curso, id_actividad, comentario)

    cursor.close()
    conn.close()



def run_verificar_actividades():
    while True:
        print("Iniciando verificación de actividades...")  # Debugging
        try:
            verificar_actividades()
        except Exception as e:
            print(f"Ocurrió un error: {e}")
        time.sleep(60)


#@app.on_event("startup")
#async def startup_event():
#    t = threading.Thread(target=run_verificar_actividades)
#    t.daemon = True
#    t.start()


@app.get("/unidad/{unidadId}/verificar-corpus")
async def verificar_corpus(unidadId: int):
    try:
        print("unidadId=",unidadId)
        conn = connect_db()
        cursor = conn.cursor()
        
        # Consultar la tabla corpus para la unidad dada
        cursor.execute("SELECT COUNT(*) FROM corpus WHERE id_unidad = %s", (unidadId,))
        count = cursor.fetchone()[0]

        if count == 0:
            return {"corpus_vacio": True, "message": "No hay material en el corpus."}
        else:
            return {"corpus_vacio": False}


    except Exception as e:
        print(f"Error al verificar el corpus: {e}")
        raise HTTPException(status_code=500, detail="Error al consultar el corpus")
    finally:
        cursor.close()
        conn.close()





#Se va al código del proyecto
def single_prompt(prompt):
    response = client.chat.completions.create(
        model="gpt-4o-mini",  # Este es el modelo que has utilizado; lo mantenemos como está.
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content.strip()




def extract_text_from_pdf(archivo_bytes):
    # Abrir el archivo PDF desde los bytes
    documento = fitz.open(stream=archivo_bytes, filetype="pdf")

    # Variable para almacenar todo el texto
    texto_completo = ""

    # Iterar sobre cada página y extraer texto
    for pagina in documento:
        texto = pagina.get_text()
        texto_completo += texto

    # Cerrar el documento
    documento.close()

    return texto_completo


#Se va al código del proyecto
def req_desafio_to_json(req):
    # Prompt para extraer los requerimientos del desafío de programación
    prompt = f"Extrae los requerimientos del documento en formato json, identifica el nombre del requerimiento, la descripción que tiene y el puntaje asociado. Los datos son de un pdf parseado, si alguno de los elementos viende una lista intenta recontruirla. El texto con el que tienes que trabajar es el siguiente: {req}"
    return single_prompt(prompt)


# se va al proyecto
def extract_text_from_codefiles(path): 
    files_to_read = [".html", ".css"]
    content = ""
    for root, dirs, files in os.walk(path): #Capaz se pueda borrar el dirs acá. Aparentemente itera por toda la carpeta.
        for file in files:
            #lower() to avoid case sensitive
            lfile = file.lower() #Toma el nombre del archivo, y lo transforma en minúsculas.

            content += "Archivo: " + lfile + "\n" #Primera linea del prompt, aparentemente.
            if lfile.endswith(tuple(files_to_read)):
                try:
                    with open(os.path.join(root, file), "r", encoding="utf-8") as f:
                        content += f.read()
                        content += "\n\n"
                except Exception as e:
                    print(f"Error al leer el archivo {file}: {e}")
    #Me imagino que retornará un texto de este estilo:
    #Archivo: ./proyecto/e3.html
    #<html>
    #<head>
    #<style>
    #Algo así. Es como un texto con todo el contenido del codigo. y todos los códigos.
    return content




def es_imagen(filename):
    # Lista de extensiones de archivos de imagen comunes
    extensiones_imagen = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.svg', '.ico']
    return any(filename.lower().endswith(ext) for ext in extensiones_imagen)

async def procesar_archivo_tar(archivo_tar: UploadFile):
    # Lista para almacenar los archivos y su contenido
    contenido_json = []

    # Convertir el archivo UploadFile en algo que tarfile pueda procesar
    file_like_object = io.BytesIO(await archivo_tar.read())

    # Abrir el archivo .tar
    with tarfile.open(fileobj=file_like_object, mode='r') as tar:
        # Iterar sobre cada miembro del archivo .tar
        for member in tar.getmembers():
            # Si el miembro es un archivo y no una carpeta
            if member.isfile():
                archivo_info = {
                    "nombre": member.name,
                    "tipo": "imagen" if es_imagen(member.name) else "texto",
                    "contenido": None
                }

                # Verificar si es un archivo de imagen
                if es_imagen(member.name):
                    print(f"Archivo de imagen registrado: {member.name}")
                    archivo_info["contenido"] = f"Archivo de imagen ignorado."
                else:
                    # Extraer el archivo
                    file = tar.extractfile(member)

                    # Verificar si el archivo es legible
                    if file is not None:
                        try:
                            # Leer el contenido del archivo
                            content = file.read().decode('utf-8', errors='ignore')
                            archivo_info["contenido"] = content
                            print(f"Contenido del archivo {member.name} guardado en la lista JSON.")
                        except Exception as e:
                            archivo_info["contenido"] = f"Error al leer el archivo {member.name}: {e}"

                # Agregar a la lista JSON
                contenido_json.append(archivo_info)
    
    # Convertir la lista a formato JSON
    return json.dumps(contenido_json, ensure_ascii=False, indent=2)

@app.get("/usuarios-cursos")
async def get_users_with_courses():
    try:
        conn = connect_db()  # Conexión a la base de datos
        cursor = conn.cursor()

        
        # Consulta para obtener todos los usuarios y sus cursos
        cursor.execute("""
            SELECT u.id AS usuario_id, u.nombre AS usuario_nombre, u.tipo, u.correo, u.direccion, u.numero_cel,
                   c.id AS curso_id, c.nombre AS curso_nombre
            FROM usuario u
            LEFT JOIN usuario_curso uc ON u.id = uc.id_usuario
            LEFT JOIN curso c ON uc.id_curso = c.id
        """)
        usuarios_con_cursos = cursor.fetchall()  # Obtenemos los resultados
        
        cursor.close()
        conn.close()
        # Agrupar los usuarios con sus cursos
        usuarios = {}
        for item in usuarios_con_cursos:
            # Si el usuario ya existe en el diccionario, agregamos el curso a su lista
            if item['usuario_id'] not in usuarios:
                usuarios[item['usuario_id']] = {
                    'id': item['usuario_id'],  # Aseguramos que el id del usuario esté en la respuesta
                    'nombre': item['usuario_nombre'],
                    'tipo': item['tipo'],
                    'correo': item['correo'],
                    'direccion': item['direccion'],
                    'numero_cel': item['numero_cel'],
                    'cursos': []
                }
            if item['curso_id']:
                usuarios[item['usuario_id']]['cursos'].append({
                    'curso_id': item['curso_id'],
                    'curso_nombre': item['curso_nombre']
                })
        
        # Convertimos el diccionario de usuarios en una lista
        usuarios_lista = list(usuarios.values())
        
        return usuarios_lista  # Devolvemos los usuarios con sus cursos
    except Exception as e:
        print(f"Error al obtener usuarios y cursos: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
@app.put("/editar_usuario/{user_id}")
async def update_user(user_id: int, request: Request):
    data = await request.json()
    nombre = data.get('nombre')
    tipo = data.get('tipo')
    correo = data.get('correo')
    direccion = data.get('direccion')
    numero_cel = data.get('numero_cel')
    # Validaciones
    if not nombre or not correo or not tipo:
        raise HTTPException(status_code=400, detail="Nombre, correo y tipo son requeridos")
    try:
        conn = connect_db()
        cursor = conn.cursor()
        # Consulta para actualizar los datos del usuario (sin cambiar id ni clave)
        update_query = """
            UPDATE usuario
            SET nombre = %s, tipo = %s, correo = %s, direccion = %s, numero_cel = %s
            WHERE id = %s
        """
        cursor.execute(update_query, (nombre, tipo, correo, direccion, numero_cel, user_id))
        # Verificar si se actualizó el usuario
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        conn.commit()
        cursor.close()
        conn.close()
        return {"message": "Usuario actualizado exitosamente"}
    except Exception as e:
        print(f"Error al actualizar el usuario: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
    
# Ruta para obtener todos los cursos
@app.get("/cursos")
async def obtener_todos_los_cursos():
    try:
        conn = connect_db()
        cursor = conn.cursor()
        # Consulta para obtener todos los cursos
        cursor.execute("SELECT id, nombre FROM curso")
        cursos = cursor.fetchall()
        cursor.close()
        conn.close()
        if cursos:
            # Devolver todos los cursos en una lista
            return [
                {
                    "curso_id": curso[0],
                    "nombre": curso[1]
                }
                for curso in cursos
            ]
        else:
            return {"message": "No se encontraron cursos"}
    except Exception as e:
        print(f"Error al obtener los cursos: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
    
@app.post("/inscribir-curso")
async def inscribir_curso(request: Request):
    try:
        # Obtener el body de la solicitud (JSON)
        body = await request.json()
        
        # Extraer los valores de id_usuario y id_curso del body
        id_usuario = body.get("id_usuario")
        id_curso = body.get("id_curso")
        
        if not id_usuario or not id_curso:
            raise HTTPException(
                status_code=400,
                detail="Faltan parámetros: id_usuario o id_curso"
            )
        
        # Conectar a la base de datos
        conn = connect_db()
        if conn is None:
            raise HTTPException(
                status_code=500,
                detail="Error al conectar con la base de datos"
            )
        
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                """
                INSERT INTO usuario_curso (id_usuario, id_curso)
                VALUES (%s, %s)
                RETURNING id
                """,
                (id_usuario, id_curso)
            )

            usuario_curso_id = cursor.fetchone()[0]
            conn.commit()

            return {
                "message": "Usuario inscrito en el curso exitosamente",
                "usuario_curso_id": usuario_curso_id
            }

        except Exception as e:
            print("❌ Error al insertar los datos en Postgres:", e)
            conn.rollback()
            raise HTTPException(
                status_code=500,
                detail="Error al insertar en la base de datos"
            )

    except HTTPException:
        raise  # 👈 deja pasar errores controlados

    except Exception as e:
        print("❌ Error general en /inscribir-curso:", e)
        raise HTTPException(
            status_code=500,
            detail="Error interno del servidor"
        )

@app.delete("/desinscribir-curso/{id_usuario}/{id_curso}")
async def desinscribir_curso(id_usuario: int, id_curso: int):
    try:
        # Conectar a la base de datos
        conn = connect_db()
        cursor = conn.cursor()
        # Eliminar la relación entre el usuario y el curso de la tabla usuario_curso
        cursor.execute("""
            DELETE FROM usuario_curso
            WHERE id_usuario = %s AND id_curso = %s
        """, (id_usuario, id_curso))
        # Asegúrate de hacer commit para guardar los cambios
        conn.commit()
        # Verificar si se eliminó algún registro
        if cursor.rowcount == 0:
            return JSONResponse(status_code=404, content={"message": "No se encontró la relación para eliminar."})
        cursor.close()
        conn.close()
        return JSONResponse(status_code=200, content={"message": "Curso desinscrito correctamente"})
    except Exception as e:
        print(f"Error al eliminar la relación entre usuario y curso: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
@app.get("/curso/{curso_id}/usuarios")
async def get_users_in_course(curso_id: int):
    try:
        conn = connect_db()  # Conexión a la base de datos
        cursor = conn.cursor()

        
        # Consulta para obtener los usuarios inscritos en el curso con curso_id específico
        cursor.execute("""
            SELECT u.id AS usuario_id, u.nombre AS usuario_nombre, u.tipo, u.correo, u.direccion, u.numero_cel
            FROM usuario u
            JOIN usuario_curso uc ON u.id = uc.id_usuario
            JOIN curso c ON uc.id_curso = c.id
            WHERE c.id = %s
        """, (curso_id,))
        
        usuarios_en_curso = cursor.fetchall()  # Obtenemos los resultados
        
        cursor.close()
        conn.close()
        # Si no hay usuarios inscritos en ese curso
        if not usuarios_en_curso:
            raise HTTPException(status_code=404, detail="No hay usuarios inscritos en este curso.")
        # Devolvemos los usuarios inscritos en el curso
        return usuarios_en_curso
    except Exception as e:
        print(f"Error al obtener usuarios inscritos en el curso {curso_id}: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

@app.get("/usuario/{user_id}/actividades")
async def get_user_activities(user_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        
        # Consulta para obtener todas las actividades de los cursos en los que el usuario está inscrito
        cursor.execute("""
            SELECT a.id AS actividad_id, 
                   a.titulo AS actividad_titulo, 
                   a.descripcion AS actividad_descripcion,
                   a.fecha_inicio, 
                   a.fecha_cierre, 
                   DATE_FORMAT(a.hora_inicio, '%H:%i') AS hora_inicio,  -- Formatear hora de inicio
                   DATE_FORMAT(a.hora_cierre, '%H:%i') AS hora_cierre,  -- Formatear hora de cierre
                   u.nombre AS unidad_nombre, 
                   c.nombre AS curso_nombre
            FROM actividad a
            JOIN unidad u ON a.id_unidad = u.id  -- Relación entre actividad y unidad
            JOIN curso c ON u.id_curso = c.id  -- Relación entre unidad y curso
            JOIN usuario_curso uc ON c.id = uc.id_curso  -- Relación entre curso y usuario_curso
            WHERE uc.id_usuario = %s  -- Filtrar por el id del usuario
        """, (user_id,))
        
        actividades = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        return actividades
    
    except Exception as e:
        print(f"Error al obtener actividades del usuario: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")


@app.post("/reportedeerror")
async def reportedeerror(
    nombre: str = Form(...),  # Nuevo campo para el nombre
    titulo: str = Form(...),
    descripcion: str = Form(...),
    correo: str = Form(...),
    fechaHora: str = Form(...),
    imagen: UploadFile = None  # La imagen es opcional
):
    try:
        # Configuración del mensaje de correo
        mensaje = MIMEMultipart()
        mensaje["From"] = EMAIL_SENDER
        mensaje["To"] = "gohanandliliscode@gmail.com"
        mensaje["Subject"] = f"Reporte de error de usuario: {correo}"

        # Contenido del mensaje
        body = f"""
        El usuario "{nombre}" ha enviado un reporte de error.

        Título: {titulo}
        Descripción: {descripcion}
        Hora: {fechaHora}
        Correo de contacto: {correo}
        """
        mensaje.attach(MIMEText(body, "plain"))

        # Adjuntar la imagen si existe
        if imagen:
            image_data = await imagen.read()  # Lee el archivo de imagen
            image_type = imghdr.what(None, h=image_data) or 'jpeg'  # Usa 'jpeg' por defecto si no detecta

            image_attachment = MIMEImage(image_data, _subtype=image_type)
            image_attachment.add_header(
                'Content-Disposition',
                'attachment',
                filename=imagen.filename  # Nombre del archivo adjunto
            )
            mensaje.attach(image_attachment)

        # Enviar el correo
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, "gohanandliliscode@gmail.com", mensaje.as_string())

        return JSONResponse(content={"message": "Reporte enviado exitosamente."})

    except Exception as e:
        print(f"Error al enviar el correo: {e}")
        raise HTTPException(status_code=500, detail="Error al enviar el correo.")
#---------------------------------------- THESIS
@app.get("/evaluaciones")
async def obtener_evaluaciones():
    try:
        conn = connect_db()
        cursor = conn.cursor()


        cursor.execute("""
            SELECT id, titulo, dificultad 
            FROM evaluacion
        """)
        evaluaciones = cursor.fetchall()

        cursor.close()
        conn.close()

        if not evaluaciones:
            raise HTTPException(status_code=404, detail="No hay evaluaciones disponibles.")

        return {"evaluaciones": evaluaciones}
    except Exception as e:
        print(f"Error al obtener evaluaciones: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

class EvaluationResponse(BaseModel):
    respuestas: dict

@app.get("/evaluaciones/{evaluacion_id}/preguntas")
async def obtener_preguntas(evaluacion_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()


        # Recuperar la dificultad de la evaluación
        cursor.execute("""
            SELECT dificultad 
            FROM evaluacion
            WHERE id = %s
        """, (evaluacion_id,))
        evaluacion = cursor.fetchone()
        if not evaluacion:
            raise HTTPException(status_code=404, detail="Evaluación no encontrada.")

        # Recuperar preguntas de tipo desarrollo
        cursor.execute("""
            SELECT id, enunciado 
            FROM desarrollo 
            WHERE id_evaluacion = %s
        """, (evaluacion_id,))
        preguntas_desarrollo = cursor.fetchall()

        # Recuperar preguntas de tipo verdadero/falso
        cursor.execute("""
            SELECT id, enunciado 
            FROM vf 
            WHERE id_evaluacion = %s
        """, (evaluacion_id,))
        preguntas_vf = cursor.fetchall()

        # Recuperar preguntas de opción múltiple
        cursor.execute("""
            SELECT id, enunciado, respuesta_a, respuesta_b, respuesta_c, respuesta_d, respuesta_e 
            FROM alternativas 
            WHERE id_evaluacion = %s
        """, (evaluacion_id,))
        preguntas_alternativas = cursor.fetchall()

        cursor.close()
        conn.close()

        if not (preguntas_desarrollo or preguntas_vf or preguntas_alternativas):
            raise HTTPException(status_code=404, detail="No hay preguntas para esta evaluación.")

        return {
            "nivel": evaluacion["dificultad"],  # Agregamos el nivel de dificultad
            "desarrollo": preguntas_desarrollo,
            "vf": preguntas_vf,
            "alternativas": preguntas_alternativas
        }
    except Exception as e:
        print(f"Error al obtener preguntas: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")


@app.post("/evaluaciones/{evaluacion_id}/evaluar")
async def evaluar_respuestas(evaluacion_id: int, respuestas: dict):
    try:
        conn = connect_db()
        cursor = conn.cursor()


        correctas = 0
        incorrectas = 0

        # Recuperar la dificultad actual de la evaluación
        cursor.execute("""
            SELECT dificultad
            FROM evaluacion
            WHERE id = %s
        """, (evaluacion_id,))
        evaluacion = cursor.fetchone()
        if not evaluacion:
            raise HTTPException(status_code=404, detail="Evaluación no encontrada.")

        dificultad_actual = evaluacion["dificultad"].lower()  # Convertir a minúsculas para evitar errores

        # Procesar respuestas
        respuestas = respuestas.get("respuestas", {})
        for pregunta_id, respuesta in respuestas.get("desarrollo", {}).items():
            cursor.execute("""
                SELECT respuesta
                FROM desarrollo
                WHERE id = %s AND id_evaluacion = %s
            """, (pregunta_id, evaluacion_id))
            correcta = cursor.fetchone()
            if correcta and respuesta.strip().lower() == correcta["respuesta"].strip().lower():
                correctas += 1
            else:
                incorrectas += 1

        for pregunta_id, respuesta in respuestas.get("vf", {}).items():
            cursor.execute("""
                SELECT correcta
                FROM vf
                WHERE id = %s AND id_evaluacion = %s
            """, (pregunta_id, evaluacion_id))
            correcta = cursor.fetchone()
            respuesta_mapeada = "v" if respuesta.lower() == "verdadero" else "f" if respuesta.lower() == "falso" else None
            if correcta and respuesta_mapeada == correcta["correcta"].lower():
                correctas += 1
            else:
                incorrectas += 1

        for pregunta_id, respuesta in respuestas.get("alternativas", {}).items():
            cursor.execute("""
                SELECT respuesta_a, respuesta_b, respuesta_c, respuesta_d, respuesta_e, correcta
                FROM alternativas
                WHERE id = %s AND id_evaluacion = %s
            """, (pregunta_id, evaluacion_id))
            correcta = cursor.fetchone()
            respuesta_mapeada = None
            if correcta:
                for letra, texto in zip(["a", "b", "c", "d", "e"], 
                                        [correcta["respuesta_a"], correcta["respuesta_b"], correcta["respuesta_c"], correcta["respuesta_d"], correcta["respuesta_e"]]):
                    if texto and respuesta.strip().lower() == texto.strip().lower():
                        respuesta_mapeada = letra
                        break

            if correcta and respuesta_mapeada == correcta["correcta"].lower():
                correctas += 1
            else:
                incorrectas += 1

        total = correctas + incorrectas
        porcentaje_correctas = correctas / total if total > 0 else 0

        # Ajustar sugerencia según nivel actual y porcentaje
        niveles = ["facil", "intermedio", "avanzado"]
        nivel_index = niveles.index(dificultad_actual) if dificultad_actual in niveles else -1
        sugerencia = "Mantener nivel actual"
        print(dificultad_actual)
        print(nivel_index)
        if porcentaje_correctas > 0.75 and nivel_index < len(niveles) - 1:  # Más del 80% correctas y no en nivel máximo
            sugerencia = f"Subir a nivel {niveles[nivel_index + 1]}"
            print(niveles[nivel_index + 1])
        elif porcentaje_correctas < 0.5 and nivel_index > 0:  # Menos del 50% correctas y no en nivel mínimo
            sugerencia = f"Bajar a nivel {niveles[nivel_index - 1]}"
            print(niveles[nivel_index -1 ])
        print(porcentaje_correctas)
        cursor.close()
        conn.close()

        return {
            "mensaje": f"Evaluación completada.",
            "porcentaje_correctas": round(porcentaje_correctas * 100, 2),  # Enviar porcentaje como porcentaje
            "sugerencia": sugerencia
        }
    except Exception as e:
        print(f"Error al evaluar respuestas: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

@app.post("/unidad/{unidad_id}/generar_preguntas")
async def generar_preguntas(unidad_id: int, request: Request):
    data = await request.json()
    preguntas_vf = data.get("vf", 0)  # Cantidad específica de VF
    preguntas_desarrollo = data.get("desarrollo", 0)  # Cantidad específica de Desarrollo
    preguntas_alternativas = data.get("alternativas", 0)  # Cantidad específica de Alternativas
    dificultad = data.get("dificultad", "facil")

    print(f"Datos recibidos: VF={preguntas_vf}, Desarrollo={preguntas_desarrollo}, Alternativas={preguntas_alternativas}, Dificultad={dificultad}")

    # Obtener assistant_id basado en unidad_id
    conn = connect_db()
    if conn is None:
        raise HTTPException(status_code=500, detail="Error al conectar a la base de datos")
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT assistant_id FROM unidad WHERE id = %s", (unidad_id,))
        result = cursor.fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="Unidad no encontrada")
        
        assistant_id = result[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al consultar la base de datos: {e}")
    finally:
        cursor.close()
        conn.close()

    # Llamada a la API de OpenAI para generar preguntas
    try:
        url = f"{url_api}/crear-preguntas/{assistant_id}"
        params = {
            'vf': preguntas_vf,
            'desarrollo': preguntas_desarrollo,
            'alternativas': preguntas_alternativas,
            'dificultad': dificultad
        }

        print(f"Parámetros enviados a la API: {params}")  # Verifica que los parámetros sean correctos

        response = requests.post(url, params=params)

        if response.status_code == 200:
            preguntas, thread_id = response.json()  # Solo devolvemos preguntas
            print(f"Preguntas generadas: {preguntas}")
            return JSONResponse(status_code=200, content={"preguntas": preguntas})

        else:
            print(f"Error {response.status_code}: {response.text}")
            raise HTTPException(status_code=response.status_code, detail=response.text)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al generar preguntas: {e}")




#################################################################
##################
################## Funcion para crear guión de clase
##################
################################################################

# Ruta para crear un nuevo guion de clase en una unidad


from fastapi import Request, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
import requests
import json
from fastapi import UploadFile
import fitz  # PyMuPDF

async def procesar_pdf(archivo: UploadFile):
    if not archivo:
        raise ValueError("No se recibió archivo")

    # Leer todo el archivo en memoria
    archivo_bytes = await archivo.read()
    if not archivo_bytes:
        raise ValueError("El archivo PDF está vacío")

    print("🔹 Archivo recibido:", archivo.filename)
    print("   Tipo MIME:", archivo.content_type)
    print("   Tamaño del archivo (bytes):", len(archivo_bytes))

    # Abrir PDF desde bytes
    try:
        documento = fitz.open(stream=archivo_bytes, filetype="pdf")
    except Exception as e:
        raise ValueError(f"No se pudo abrir el PDF: {e}")

    # Extraer texto
    texto = ""
    for pagina in documento:
        texto += pagina.get_text()

    return texto



@app.post("/unidad/{unidad_id}/crear-guion")
async def crear_guion(
    unidad_id: int,
    titulo: str = Form(...),
    ra: str = Form(...),
    contenido: str = Form(...),
    estilo: str = Form(...),
    duracion: str = Form(...),
    semana: str = Form(...),
    archivo: UploadFile = File(...)
):
    print("📥 Recibiendo solicitud para crear guion de clase...")
    print(f"📋 Nuevos campos - Duración: {duracion}, Semana: {semana}")

    # Validar que duración y semana sean números válidos
    try:
        duracion_int = int(duracion)
        semana_int = int(semana)
        if duracion_int <= 0 or semana_int <= 0:
            raise ValueError("La duración y semana deben ser números positivos")
    except ValueError as e:
        print(f"❌ Error validando duración o semana: {e}")
        raise HTTPException(status_code=400, detail="Duración y semana deben ser números válidos mayores a 0")

    # -------------------- Obtener datos de unidad, curso y profesor --------------------
    try:
        conn = connect_db()
        cursor = conn.cursor()

        
        # Obtener información de unidad, curso y profesor
        cursor.execute("""
            SELECT 
                u.nombre as nombre_unidad,
                c.nombre as nombre_curso,
                us.nombre as nombre_profesor
            FROM unidad u
            JOIN curso c ON u.id_curso = c.id
            JOIN usuario_curso uc ON c.id = uc.id_curso
            JOIN usuario us ON uc.id_usuario = us.id
            WHERE u.id = %s
            AND us.tipo = 2
            LIMIT 1
        """, (unidad_id,))
        
        info_data = cursor.fetchone()
        
        if not info_data:
            raise HTTPException(status_code=404, detail="No se encontró la unidad, curso o profesor")
        
        nombre_unidad = info_data["nombre_unidad"]
        nombre_curso = info_data["nombre_curso"]
        nombre_profesor = info_data["nombre_profesor"]
        
        print(f"📊 Datos obtenidos: Unidad={nombre_unidad}, Curso={nombre_curso}, Profesor={nombre_profesor}")
        
    except Exception as e:
        print("❌ Error obteniendo datos de unidad/curso/profesor:", e)
        raise HTTPException(status_code=500, detail="Error al obtener información de la unidad")

    # -------------------- Crear assistant y vector --------------------
    try:
        print("🤖 Creando nuevo assistant y vector para el guion...")
        assistant_id, vector_id = crear_assistant()
        print(f"✅ Assistant creado: {assistant_id} | Vector creado: {vector_id}")

        await archivo.seek(0)
        resultado_subida = await subir_corpus(assistant_id, archivo, vector_id)
        file_id = resultado_subida["file_id"]

        print(f"📤 Archivo subido y vinculado correctamente")

    except Exception as e:
        print("❌ Error creando assistant o subiendo archivo:", e)
        raise HTTPException(status_code=500, detail=f"Error al crear assistant o subir archivo: {e}")

    # -------------------- Guardar guion_clase --------------------
    try:
        cursor.execute(
            """
            INSERT INTO guion_clase (
                titulo, ra, contenido, estilo, duracion, semana,
                id_unidad, thread, assistant_id, vector_id, file_id, file_name
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                titulo, ra, contenido, estilo, duracion_int, semana_int,
                unidad_id, None, assistant_id, vector_id, file_id, archivo.filename
            )
        )

        guion_id = cursor.fetchone()["id"]
        conn.commit()
        print(f"✅ Guion guardado con ID: {guion_id}")

    except Exception as e:
        print("❌ Error insertando guion_clase:", e)
        raise

    # -------------------- Enviar datos a GPT --------------------
    try:
        url = f"{url_api}/crear_guion/{assistant_id}"
        data = {
            "titulo": titulo,
            "resultado_aprendizaje": ra,
            "contenido_tematico": contenido,
            "tipo_clase": estilo,
            "duracion": duracion_int,
            "semana": semana_int,
            "vector_id": vector_id
        }
        print("🤖 Enviando datos a la API de GPT...")
        print(f"📤 URL: {url}")
        
        response = requests.post(url, data=data)
        print(f"📥 Status Code: {response.status_code}")
        
        response.raise_for_status()
        
        print("✅ Llamada a GPT completada")

    except requests.exceptions.RequestException as e:
        print("❌ Error llamando a GPT:", e)
        print(f"❌ Response text: {response.text if 'response' in locals() else 'No response'}")
        raise HTTPException(status_code=500, detail=f"Error al comunicarse con GPT: {e}")

    # -------------------- Procesar respuesta de GPT --------------------
    data_response = response.json()
    thread_id = data_response.get("thread_id")

    print(f"📦 thread_id recibido: {thread_id}")

    # -------------------- Guardar planificación (FORMATO DOCENTE) --------------------
    try:
        cursor.execute("""
            INSERT INTO planificacion (
                id_guion_clase,
                titulo,
                resultado_aprendizaje,
                contenido_tematico,
                tipo_clase,
                duracion,
                semana,
                vector_id,
                identificacion_clase,
                secuencia_actividades,
                evaluaciones_formativas,
                estrategias_didacticas,
                bibliografia_material,
                analisis_ra,
                metadata,
                thread_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            guion_id,
            titulo,
            ra,
            contenido,
            estilo,
            duracion_int,
            semana_int,
            vector_id,

            json.dumps(data_response.get("identificacion_clase", {}), ensure_ascii=False),
            json.dumps(data_response.get("secuencia_actividades", {}), ensure_ascii=False),
            json.dumps(data_response.get("evaluaciones_formativas", []), ensure_ascii=False),
            json.dumps(data_response.get("estrategias_didacticas", []), ensure_ascii=False),
            json.dumps(data_response.get("bibliografia_material", []), ensure_ascii=False),

            json.dumps(data_response.get("analisis_ra", {}), ensure_ascii=False),
            json.dumps(data_response.get("metadata", {}), ensure_ascii=False),
            data_response.get("thread_id", thread_id)
        ))
        conn.commit()
        print("✅ Planificación guardada en DB (formato docente)")
    except Exception as e:
        print("❌ Error insertando en planificacion:", e)
        raise

    # -------------------- Actualizar thread --------------------
    try:
        cursor.execute("UPDATE guion_clase SET thread = %s WHERE id = %s", (thread_id, guion_id))
        conn.commit()
        cursor.close()
        conn.close()
        print("✅ Thread actualizado en guion_clase")
        
    except Exception as e:
        print("❌ Error actualizando thread en guion_clase:", e)
        raise

    # -------------------- Construir respuesta FINAL --------------------
    respuesta_final = {
        "identificacion_clase": data_response.get("identificacion_clase", {}),
        "resultado_aprendizaje": ra,                 # input (igual lo puedes devolver)
        "contenido_tematico": contenido,             # input
        "analisis_ra": data_response.get("analisis_ra", {}),
        "secuencia_actividades": data_response.get("secuencia_actividades", {}),
        "estrategias_didacticas": data_response.get("estrategias_didacticas", []),
        "evaluaciones_formativas": data_response.get("evaluaciones_formativas", []),
        "bibliografia_material": data_response.get("bibliografia_material", []),
        "metadata": data_response.get("metadata", {}),
        "thread_id": data_response.get("thread_id", thread_id),

        "nombre_unidad": nombre_unidad,
        "nombre_curso": nombre_curso,
        "nombre_profesor": nombre_profesor
    }


    return JSONResponse(status_code=201, content={
        "message": "Guion y planificación creados exitosamente",
        "guion_id": guion_id,
        "thread_id": thread_id,
        "vector_id": vector_id,
        "planificacion": respuesta_final
    })

################## Funcion para obtener guión de clase en curso-profesor
##################
#########################################################################

# En el endpoint que obtiene la planificación, agrega:
@app.get("/guion/{guion_id}/planificacion")
async def obtener_planificacion_guion(guion_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()


        cursor.execute("""
            SELECT 
                p.identificacion_clase,
                p.resultado_aprendizaje,
                p.contenido_tematico,
                p.analisis_ra,
                p.secuencia_actividades,
                p.estrategias_didacticas,
                p.evaluaciones_formativas,
                p.bibliografia_material,
                p.metadata,
                p.thread_id,
                u.nombre as nombre_unidad,
                c.nombre as nombre_curso,
                us.nombre as nombre_profesor
            FROM planificacion p
            JOIN guion_clase gc ON p.id_guion_clase = gc.id
            JOIN unidad u ON gc.id_unidad = u.id
            JOIN curso c ON u.id_curso = c.id
            JOIN usuario_curso uc ON c.id = uc.id_curso
            JOIN usuario us ON uc.id_usuario = us.id
            WHERE p.id_guion_clase = %s
            AND us.tipo = 2
            LIMIT 1
        """, (guion_id,))

        planificacion = cursor.fetchone()
        cursor.close()
        conn.close()

        if not planificacion:
            return {"message": "No hay planificación para este guion"}

        # Reconstruir estructura (igual a la respuesta nueva de tu /crear_guion)
        result = {
            "identificacion_clase": json.loads(planificacion.get("identificacion_clase") or "{}"),
            "resultado_aprendizaje": planificacion.get("resultado_aprendizaje", ""),
            "contenido_tematico": planificacion.get("contenido_tematico", ""),
            "analisis_ra": json.loads(planificacion.get("analisis_ra") or "{}"),
            "secuencia_actividades": json.loads(planificacion.get("secuencia_actividades") or "{}"),
            "estrategias_didacticas": json.loads(planificacion.get("estrategias_didacticas") or "[]"),
            "evaluaciones_formativas": json.loads(planificacion.get("evaluaciones_formativas") or "[]"),
            "bibliografia_material": json.loads(planificacion.get("bibliografia_material") or "[]"),
            "metadata": json.loads(planificacion.get("metadata") or "{}"),
            "thread_id": planificacion.get("thread_id", ""),

            "nombre_unidad": planificacion.get("nombre_unidad", ""),
            "nombre_curso": planificacion.get("nombre_curso", ""),
            "nombre_profesor": planificacion.get("nombre_profesor", "")
        }

        return result

    except Exception as e:
        print(f"Error al obtener planificación: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
##########################################################################
##################
################## Funcion para obtener informacion de la planificacion
##################
#########################################################################

@app.get("/unidad/{unidad_id}/planificaciones")
async def obtener_planificaciones_unidad(unidad_id: int):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT p.id AS planificacion_id, g.id AS guion_id, g.titulo
            FROM guion_clase g
            JOIN planificacion p ON g.id = p.id_guion_clase
            WHERE g.id_unidad = %s
            ORDER BY g.id DESC
        """, (unidad_id,))
        planificaciones = cursor.fetchall()
        cursor.close()
        conn.close()
        return planificaciones
    except Exception as e:
        print(f"Error al obtener planificaciones de la unidad: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")


#########################################################################
##################
################## Funcion para borrar guion
##################
#########################################################################

def eliminar_guion(id_guion: int):
    """Elimina un guión y todos sus recursos asociados en OpenAI"""
    
    try:
        # Primero obtener los IDs de los recursos antes de eliminar el registro
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()

        
        # Obtener los IDs de los recursos asociados al guión
        cursor.execute("""
            SELECT assistant_id, vector_id, file_id 
            FROM guion_clase 
            WHERE id = %s
        """, (id_guion,))
        
        guion = cursor.fetchone()
        
        if not guion:
            print(f"❌ Guión con ID {id_guion} no encontrado")
            return {"error": "Guión no encontrado"}
        
        assistant_id = guion['assistant_id']
        vector_id = guion['vector_id']
        file_id = guion['file_id']
        
        # Cerrar conexión antes de eliminar recursos externos
        cursor.close()
        conn.close()
        
        # Eliminar recursos de OpenAI (si existen)
        recursos_eliminados = []
        
        # 1. Eliminar asistente
        if assistant_id and assistant_id.strip():
            try:
                client.beta.assistants.delete(assistant_id)
                print(f"🗑️ Assistant {assistant_id} eliminado")
                recursos_eliminados.append(f"assistant_{assistant_id}")
            except Exception as e:
                if "not found" not in str(e).lower():
                    print(f"⚠️ Error eliminando assistant {assistant_id}: {e}")
        
        # 2. Eliminar vector store
        if vector_id and vector_id.strip():
            try:
                client.beta.vector_stores.delete(vector_id)
                print(f"🗑️ Vector store {vector_id} eliminado")
                recursos_eliminados.append(f"vector_{vector_id}")
            except Exception as e:
                if "not found" not in str(e).lower():
                    print(f"⚠️ Error eliminando vector store {vector_id}: {e}")
        
        # 3. Eliminar archivo
        if file_id and file_id.strip():
            try:
                client.files.delete(file_id)
                print(f"🗑️ File {file_id} eliminado")
                recursos_eliminados.append(f"file_{file_id}")
            except Exception as e:
                if "not found" not in str(e).lower():
                    print(f"⚠️ Error eliminando file {file_id}: {e}")
        
        # Ahora eliminar el registro de la base de datos
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM guion_clase WHERE id = %s", (id_guion,))
        conn.commit()
        
        cursor.close()
        conn.close()
        
        print(f"✅ Guión {id_guion} eliminado exitosamente")
        print(f"📦 Recursos eliminados: {len(recursos_eliminados)}")
        
        return {
            "message": f"Guión {id_guion} eliminado exitosamente",
            "recursos_eliminados": recursos_eliminados,
            "recursos_count": len(recursos_eliminados)
        }
        
    except Exception as e:
        print(f"❌ Error eliminando guión {id_guion}: {e}")
        return {"error": f"Error eliminando guión: {str(e)}"}

# Versión para FastAPI endpoint
@app.delete("/guion/{id_guion}")
async def eliminar_guion_endpoint(id_guion: int):
    """Endpoint para eliminar un guión y sus recursos"""
    return eliminar_guion(id_guion)


from fastapi.responses import StreamingResponse
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import tempfile
from io import BytesIO
from fastapi.responses import JSONResponse, FileResponse
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
import json
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem, Table, TableStyle
)
from datetime import datetime
from threading import Lock


##########################################################################
##################
################## Funciones auxialiares para buen funcionamiento 
##################
#########################################################################
# Estado global para throttling
estado_global = {
    'ultima_solicitud': 0,
    'lock': Lock(),
    'intervalo_minimo': 1.5  # 1.5 segundos entre solicitudes
}

async def throttling_global():
    """Controla el ritmo de todas las solicitudes a la API"""
    with estado_global['lock']:
        tiempo_actual = time.time()
        tiempo_desde_ultima = tiempo_actual - estado_global['ultima_solicitud']
        
        if tiempo_desde_ultima < estado_global['intervalo_minimo']:
            espera = estado_global['intervalo_minimo'] - tiempo_desde_ultima
            print(f"⏳ Throttling: esperando {espera:.1f}s")
            await asyncio.sleep(espera)
        
        estado_global['ultima_solicitud'] = time.time()

def validar_contenido_segun_tipo(contenido_texto, tipo_contenido):
    """
    Valida que el contenido tenga la estructura esperada según el tipo
    usando los mismos métodos que ya tienes en tus funciones
    """
    if not contenido_texto or not contenido_texto.strip():
        return False, "Contenido vacío"
    
    # ✅ NUEVO: Debugging - siempre imprimir lo que viene
    print(f"🔍 CONTENIDO RECIBIDO ({tipo_contenido}): {contenido_texto[:500]}{'...' if len(contenido_texto) > 500 else ''}")
    
    try:
        if tipo_contenido in ["glosario", "flashcards", "evaluacion"]:
            # Para estos tipos, debe ser JSON válido y tener estructura esperada
            contenido_json = None
            
            # 🔍 Usar TU método de extracción de JSON
            match = re.search(r'```json([\s\S]*?)```', contenido_texto)
            if match:
                contenido_json = json.loads(match.group(1))
            else:
                contenido_json = json.loads(contenido_texto)
            
            # Validar estructura específica según tipo
            if tipo_contenido == "glosario":
                # Debe tener array de términos o objeto con clave "glosario"
                if isinstance(contenido_json, dict) and "glosario" in contenido_json:
                    array_glosario = contenido_json["glosario"]
                elif isinstance(contenido_json, list):
                    array_glosario = contenido_json
                else:
                    return False, "Glosario no tiene estructura esperada"
                
                # Validar que tenga al menos un término con estructura básica
                if array_glosario and isinstance(array_glosario[0], dict):
                    primer_termino = array_glosario[0]
                    if "termino" in primer_termino and "definicion" in primer_termino:
                        return True, f"Glosario válido con {len(array_glosario)} términos"
                return False, "Glosario sin términos válidos"
            
            elif tipo_contenido == "flashcards":
                # Debe tener array de flashcards o objeto con clave "flashcards"
                if isinstance(contenido_json, dict) and "flashcards" in contenido_json:
                    array_flashcards = contenido_json["flashcards"]
                elif isinstance(contenido_json, list):
                    array_flashcards = contenido_json
                else:
                    return False, "Flashcards no tiene estructura esperada"
                
                # Validar que tenga al menos una flashcard con estructura básica
                if array_flashcards and isinstance(array_flashcards[0], dict):
                    primer_card = array_flashcards[0]
                    if "pregunta" in primer_card and "respuesta" in primer_card:
                        return True, f"Flashcards válidas con {len(array_flashcards)} cards"
                
                # ✅ CORRECCIÓN: Faltaba este return False
                return False, "Flashcards sin cards válidas"
            
            elif tipo_contenido == "evaluacion":
                # Para evaluación, validar según tipo_instrumento (si está disponible)
                # Validación básica: que sea un objeto JSON y tenga contenido
                if isinstance(contenido_json, dict) and contenido_json:
                    # Verificar que tenga al menos alguna estructura esperada
                    if any(key in contenido_json for key in ["preguntas", "actividades", "proyecto"]):
                        return True, "Evaluación con estructura válida"
                    elif contenido_json:  # Si tiene cualquier contenido
                        return True, "Evaluación JSON válida"
                return False, "Evaluación sin estructura válida"
        
        elif tipo_contenido == "resumen":
            # ✅ CORREGIDO: Para resumen, ser más estricto con contenido vacío
            try:
                # Intentar parsear como JSON
                contenido_json = json.loads(contenido_texto)
                if isinstance(contenido_json, dict) and contenido_json:
                    # Verificar que tenga contenido real, no solo estructura vacía
                    contenido_str = str(contenido_json)
                    if len(contenido_str) > 50:  # Mínimo 50 caracteres de contenido
                        return True, f"Resumen JSON válido ({len(contenido_str)} chars)"
                    else:
                        return False, "Resumen JSON demasiado corto"
                elif isinstance(contenido_json, dict):
                    return False, "Resumen JSON vacío"
            except json.JSONDecodeError:
                # Si no es JSON, verificar que sea texto con contenido sustancial
                texto_limpio = contenido_texto.strip()
                if len(texto_limpio) > 100:  # ✅ AUMENTADO: mínimo 100 caracteres para texto plano
                    return True, f"Resumen texto válido ({len(texto_limpio)} chars)"
                else:
                    return False, f"Resumen texto demasiado corto ({len(texto_limpio)} chars)"
        
        elif tipo_contenido == "mapa_conceptual":
            # Para mapa conceptual, usar TU validación completa
            mapa_texto_limpio = contenido_texto.strip()
            json_str = None

            # Usar TU lógica de extracción
            if mapa_texto_limpio.startswith('{'):
                json_str = mapa_texto_limpio
            else:
                match = re.search(r'```json\s*([\s\S]*?)\s*```', contenido_texto)
                if match:
                    json_str = match.group(1).strip()
                else:
                    match_json = re.search(r'\{[\s\S]*\}', mapa_texto_limpio)
                    if match_json:
                        json_str = match_json.group(0)
            
            if not json_str:
                return False, "No se encontró JSON en mapa conceptual"
            
            # Intentar parsear con TU función de limpieza
            try:
                json_limpio = limpiar_json(json_str)
                mapa_json = json.loads(json_limpio)
                
                # Validar estructura básica del mapa
                if isinstance(mapa_json, dict) and mapa_json:
                    # Puede tener la estructura antigua o nueva
                    if "conceptos_principales" in mapa_json or "conceptos" in mapa_json:
                        return True, "Mapa conceptual con estructura válida"
                    else:
                        return False, "Mapa conceptual sin estructura reconocida"
                else:
                    return False, "Mapa conceptual JSON vacío"
                
            except (json.JSONDecodeError, Exception) as e:  # ✅ CORREGIDO: JSONDecodeError
                return False, f"Error parseando mapa: {str(e)}"
        
        return False, f"Tipo de contenido no soportado: {tipo_contenido}"
        
    except Exception as e:
        return False, f"Error en validación: {str(e)}"

async def verificar_recursos_antes_de_procesar(vector_id, assistant_id):
    """
    Verifica que los recursos estén disponibles
    """
    try:
        # Verificar vector store
        vector_store = client.beta.vector_stores.retrieve(vector_id)
        files = list(client.beta.vector_stores.files.list(
            vector_store_id=vector_id, 
            limit=5
        ))
        
        if len(files) == 0:
            return False, "Vector store no tiene archivos"
            
        # Verificar assistant
        assistant = client.beta.assistants.retrieve(assistant_id)
        
        return True, f"Recursos OK: {len(files)} archivos"
        
    except Exception as e:
        return False, f"Error verificando recursos: {str(e)}"
    
async def cancelar_runs_activos_si_reintento(thread_id, intento_actual, max_intentos):
    """
    Cancela runs si estamos en reintentos y hay runs activos
    """
    if intento_actual > 0:  # Solo si es un reintento
        try:
            active_runs = client.beta.threads.runs.list(thread_id=thread_id)
            for run in active_runs:
                if run.status in ["queued", "in_progress"]:
                    print(f"🛑 Cancelando run huérfano {run.id} (reintento {intento_actual})")
                    client.beta.threads.runs.cancel(
                        thread_id=thread_id, 
                        run_id=run.id
                    )
                    await asyncio.sleep(0.5)  # Pequeña pausa
        except Exception as e:
            print(f"⚠️ Error cancelando runs: {e}")

async def llamar_api_con_reintentos_y_cancelacion(url, data, max_intentos=3, tipo_contenido="glosario"):
    """
    Versión MEJORADA CON AIOHTTP para ser asíncrona
    """
    import aiohttp
    thread_id = data.get("thread_id")
    
    for intento in range(max_intentos):
        try:
            print(f"🔄 Intento {intento + 1} de {max_intentos} para {tipo_contenido}")
            
            # ✅ Cancelar runs huérfanos en reintentos
            if intento > 0 and thread_id:
                await cancelar_runs_activos_si_reintento(thread_id, intento, max_intentos)
            
            # ✅ USAR AIOHTTP EN LUGAR DE REQUESTS (para async)
            timeout_por_tipo = {
                "mapa_conceptual": 180,
                "resumen": 60,
                "flashcards": 45,
                "glosario": 45,
                "evaluacion_formativa": 60
            }

            
            timeout = aiohttp.ClientTimeout(total=timeout_por_tipo.get(tipo_contenido, 60))
            
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, data=data) as response:
                    response.raise_for_status()
                    data_response = await response.json()
            
            # Validación de contenido
            contenido_texto = obtener_contenido_por_tipo(data_response, tipo_contenido)
            es_valido, mensaje = validar_contenido_segun_tipo(contenido_texto, tipo_contenido)
            
            if es_valido:
                print(f"✅ {mensaje} (intento {intento + 1})")
                return data_response
            else:
                print(f"🚫 Contenido rechazado: {mensaje} (intento {intento + 1})")
                continue
                
        except asyncio.TimeoutError:
            print(f"⏰ Timeout en intento {intento + 1}")
            continue
            
        except Exception as e:
            print(f"❌ Error en intento {intento + 1}: {e}")
            continue
        
        # Backoff exponencial entre intentos
        if intento < max_intentos - 1:
            tiempo_espera = 2 ** intento
            print(f"⏳ Esperando {tiempo_espera}s antes del próximo intento...")
            await asyncio.sleep(tiempo_espera)
    
    print(f"💀 Todos los intentos fallaron para {tipo_contenido}")
    return None


def obtener_contenido_por_tipo(data_response, tipo_contenido):
    """
    Extrae el contenido del response según el tipo
    """
    if tipo_contenido == "glosario":
        return data_response.get("glosario", "")
    elif tipo_contenido == "flashcards":
        return data_response.get("flashcards", "")
    elif tipo_contenido == "evaluacion":
        return data_response.get("evaluacion", "")
    elif tipo_contenido == "resumen":
        return data_response.get("resumen", "")
    elif tipo_contenido == "mapa_conceptual":
        return data_response.get("mapa_conceptual", "")
    elif tipo_contenido == "evaluacion_formativa":
        return data_response.get("evaluacion_formativa")
    else:
        return ""

#########################################################################
#########################################################################
#########################################################################


#########################################################################
##################
################## Funcion para generar resumen 
##################
#########################################################################
@app.get("/planificacion/{guion_id}/resumen")
async def generar_resumen(guion_id: int, accion: str = Query("obtener")):
    print(f"📥 Generando resumen - Acción: {accion}")
    thread_id = None
    conn = None
    cursor = None

    try:
        conn = connect_db()
        cursor = conn.cursor()

        
        # PRIMERO: Verificar si ya existe resumen y no se quiere regenerar
        if accion == "obtener":
            cursor.execute("""
                SELECT tema_principal, ideas_principales, conceptos_clave, conclusion, metadata
                FROM resumen 
                WHERE id_guion_clase = %s 
                ORDER BY version DESC 
                LIMIT 1
            """, (guion_id,))
            
            resumen_existente = cursor.fetchone()
            if resumen_existente:
                print("✅ Resumen encontrado en BD, devolviendo...")
                
                # Parsear JSON de la BD
                ideas_principales = json.loads(resumen_existente['ideas_principales']) if resumen_existente['ideas_principales'] else []
                conceptos_clave = json.loads(resumen_existente['conceptos_clave']) if resumen_existente['conceptos_clave'] else []
                metadata = json.loads(resumen_existente['metadata']) if resumen_existente['metadata'] else {}
                
                # Formatear resumen_json para que tenga la misma estructura
                resumen_json = {
                    "tema_principal": resumen_existente['tema_principal'],
                    "ideas_principales": ideas_principales,
                    "conceptos_clave": conceptos_clave,
                    "conclusion": resumen_existente['conclusion']
                }
                
                # Retornar EXACTAMENTE igual que antes - usando metadata guardado
                return JSONResponse({
                    "resumen": resumen_json,
                    "unidad_nombre": metadata.get("unidad_nombre", "Sin nombre"),
                    "profesor": metadata.get("profesor", "Docente no especificado"),
                    "nombre_curso": metadata.get("nombre_curso", "Curso no especificado"),
                    "nombre_asignatura": metadata.get("nombre_asignatura", "Asignatura no especificada"),
                    "nombre_unidad": metadata.get("nombre_unidad", "Unidad no especificada")
                })
        
        # SI NO EXISTE O SE QUIERE REGENERAR: continuar con tu lógica original
        print(f"🔍 Obteniendo información del guión {guion_id}")
        cursor.execute("""
            SELECT 
                g.vector_id, 
                g.assistant_id, 
                u.nombre AS unidad_nombre, 
                usr.nombre AS profesor,
                c.nombre AS nombre_curso,
                p.identificacion_clase
            FROM guion_clase g
            JOIN unidad u ON g.id_unidad = u.id
            JOIN curso c ON u.id_curso = c.id
            LEFT JOIN planificacion p ON p.id_guion_clase = g.id
            LEFT JOIN usuario_curso uc ON uc.id_curso = c.id
            LEFT JOIN usuario usr ON uc.id_usuario = usr.id
            WHERE g.id = %s AND usr.tipo = 2
            LIMIT 1
        """, (guion_id,))
        
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Guion no encontrado")
            
        # ✅ Verificar recursos ANTES de crear thread
        recursos_ok, mensaje_recursos = await verificar_recursos_antes_de_procesar(
            result['vector_id'], 
            result['assistant_id']
        )
        if not recursos_ok:
            raise HTTPException(status_code=400, detail=mensaje_recursos)
            
        # GUARDAR result para usarlo después
        result_data = {
            'vector_id': result['vector_id'],
            'assistant_id': result['assistant_id'],
            'unidad_nombre': result['unidad_nombre'],
            'profesor': result['profesor'],
            'nombre_curso': result['nombre_curso'],
            'identificacion_clase': result['identificacion_clase']
        }
            
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    try:
        # ✅ Throttling global ANTES de crear thread
        await throttling_global()
        
        # Crear thread
        nuevo_thread = client.beta.threads.create()
        thread_id = nuevo_thread.id
        print(f"🆕 Nuevo thread creado: {thread_id}")
        
        url = f"{url_api}/generar_resumen/{result_data['assistant_id']}"
        data = {
            "thread_id": thread_id,
            "vector_id": result_data['vector_id']
        }
        
        # ✅ Llamar a la API
        data_response = await llamar_api_con_reintentos_y_cancelacion(
            url, data, max_intentos=3, tipo_contenido="resumen"
        )
        
        # ✅ Procesar respuesta
        if data_response:
            resumen_texto = data_response.get("resumen", "")
        else:
            resumen_texto = json.dumps({
                "tema_principal": "Resumen de ejemplo",
                "ideas_principales": ["Concepto 1", "Concepto 2"],
                "conceptos_clave": ["Clave 1", "Clave 2"], 
                "conclusion": "Contenido generado automáticamente"
            })

        # Procesamiento del resumen (igual que antes)
        match = re.search(r'```json([\s\S]*?)```', resumen_texto)
        if match:
            try:
                resumen_json = json.loads(match.group(1))
            except json.JSONDecodeError:
                resumen_json = {"texto": resumen_texto}
        else:
            try:
                resumen_json = json.loads(resumen_texto)
            except json.JSONDecodeError:
                resumen_json = {"texto": resumen_texto}

        # Extraer nombre_asignatura (igual que antes)
        nombre_asignatura = "Asignatura no especificada"
        if result_data.get('identificacion_clase'):
            try:
                identificacion_data = json.loads(result_data['identificacion_clase'])
                nombre_asignatura = identificacion_data.get('nombre_asignatura', result_data.get('nombre_curso', 'Asignatura no especificada'))
            except:
                nombre_asignatura = result_data.get('nombre_curso', 'Asignatura no especificada')
        else:
            nombre_asignatura = result_data.get('nombre_curso', 'Asignatura no especificada')
        
        # GUARDAR EN LA BASE DE DATOS
        conn = connect_db()
        cursor = conn.cursor()
        
        # Obtener número de versión
        cursor.execute("""
            SELECT COALESCE(MAX(version), 0) + 1 as nueva_version
            FROM resumen 
            WHERE id_guion_clase = %s
        """, (guion_id,))
        
        version_result = cursor.fetchone()
        nueva_version = version_result[0] if version_result else 1
        
        # Preparar metadata para guardar (CON TODOS LOS DATOS)
        metadata = {
            "unidad_nombre": result_data.get("unidad_nombre", "Sin nombre"),
            "profesor": result_data.get("profesor", "Docente no especificado"),
            "nombre_curso": result_data.get("nombre_curso", "Curso no especificado"),
            "nombre_asignatura": nombre_asignatura,
            "nombre_unidad": result_data.get("unidad_nombre", "Unidad no especificada"),
            "vector_id": result_data.get("vector_id"),  # Opcional: guardar para referencia
            "assistant_id": result_data.get("assistant_id")  # Opcional
        }
        
        # Insertar nuevo resumen
        # BORRAR RESUMENES ANTERIORES DEL MISMO GUION
        cursor.execute("""
            DELETE FROM resumen 
            WHERE id_guion_clase = %s
        """, (guion_id,))

        # Insertar nuevo resumen (SIEMPRE con version = 1)
        cursor.execute("""
            INSERT INTO resumen (
                id_guion_clase, 
                tema_principal, 
                ideas_principales,
                conceptos_clave, 
                conclusion, 
                metadata,
                version
            ) VALUES (%s, %s, %s, %s, %s, %s, 1)  -- Versión siempre 1
        """, (
            guion_id,
            resumen_json.get("tema_principal", ""),
            json.dumps(resumen_json.get("ideas_principales", [])),
            json.dumps(resumen_json.get("conceptos_clave", [])),
            resumen_json.get("conclusion", ""),
            json.dumps(metadata)
        ))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print(f"✅ Resumen guardado en BD (v{nueva_version})")
        
        # RETORNAR EXACTAMENTE COMO SIEMPRE
        return JSONResponse({
            "resumen": resumen_json,
            "unidad_nombre": result_data.get("unidad_nombre", "Sin nombre"),
            "profesor": result_data.get("profesor", "Docente no especificado"),
            "nombre_curso": result_data.get("nombre_curso", "Curso no especificado"),
            "nombre_asignatura": nombre_asignatura,
            "nombre_unidad": result_data.get("unidad_nombre", "Unidad no especificada")
        })

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error en generación: {e}")
        raise HTTPException(status_code=500, detail="Error generando resumen")
        
    finally:
        # Limpiar thread
        if thread_id:
            try:
                client.beta.threads.delete(thread_id)
                print(f"🧹 Thread {thread_id} eliminado")
            except Exception as e:
                print(f"⚠️ No se pudo eliminar thread: {e}")            
#########################################################################
##################
################## Funcion para obtener MAPA MENTAL 
##################
#########################################################################
import subprocess
import base64



def limpiar_json(json_str):
    """
    Elimina comas sobrantes al final de objetos y arrays en JSON
    """
    json_limpio = re.sub(r',\s*([}\]])', r'\1', json_str)
    print(f"🔧 Comas sobrantes eliminadas: {json_str.count(',') - json_limpio.count(',')}")
    return json_limpio

def escapar_caracteres_mermaid(texto):
    """
    Escapa SOLO los caracteres que rompen Mermaid, sin escapado múltiple
    """
    if not texto:
        return texto
    
    # Solo escapar los caracteres críticos para Mermaid
    # NO usar &amp; aquí porque causa escapado múltiple
    replacements = {
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }
    
    texto_limpio = texto
    for char, replacement in replacements.items():
        texto_limpio = texto_limpio.replace(char, replacement)
    
    return texto_limpio
def limpiar_mapa_para_mermaid(mapa_data):
    """
    Limpia los nombres de los conceptos para que no rompan Mermaid.
    DEVUELVE EL FORMATO ORIGINAL SIN MODIFICAR LA ESTRUCTURA.
    """
    import copy
    mapa_limpio = copy.deepcopy(mapa_data)

    # 🔥 SOLO LIMPIAR CARACTERES, NO MODIFICAR ESTRUCTURA
    if "conceptos" in mapa_limpio:
        for concepto in mapa_limpio["conceptos"]:
            concepto["nombre"] = escapar_caracteres_mermaid(concepto.get("nombre", ""))
            if "descripcion" in concepto:
                concepto["descripcion"] = escapar_caracteres_mermaid(concepto["descripcion"])

    print("✅ Mapa limpiado para Mermaid (estructura original preservada)")
    return mapa_limpio

# 🚨 ELIMINA O COMENTA COMPLETAMENTE esta función
# def normalizar_estructura_api(mapa_original):
#     # COMENTA TODO ESTE CÓDIGO
#     pass
def normalizar_estructura_api(mapa_original):
    """
    Convierte la estructura de la API manteniendo la profundidad real
    """
    conceptos = mapa_original.get("conceptos", [])
    relaciones = mapa_original.get("relaciones", [])
    
    # ✅ MANTENER LA ESTRUCTURA ORIGINAL, NO FORZAR 2 NIVELES
    conceptos_principales = []
    conceptos_secundarios = []
    conceptos_terciarios = []
    
    for concepto in conceptos:
        nivel = concepto.get("nivel", "")
        id_concepto = concepto.get("id", "")
        
        if nivel == "raiz" or id_concepto == "cp_1":
            concepto["nivel"] = "principal"  # El raíz va a principales
            conceptos_principales.append(concepto)
        elif nivel == "principal" or (id_concepto.startswith("cp_1_") and id_concepto.count("_") == 1):
            conceptos_principales.append(concepto)
        elif nivel == "secundario" or (id_concepto.startswith("cp_1_") and id_concepto.count("_") == 2):
            conceptos_secundarios.append(concepto)
        elif nivel == "terciario" or (id_concepto.startswith("cp_1_") and id_concepto.count("_") == 3):
            conceptos_terciarios.append(concepto)
        else:
            # Por defecto, según el ID
            if id_concepto.count("_") <= 1:
                conceptos_principales.append(concepto)
            elif id_concepto.count("_") == 2:
                conceptos_secundarios.append(concepto)
            else:
                conceptos_terciarios.append(concepto)
    
    # 🆕 Estructura que mantiene la profundidad
    mapa_normalizado = {
        "titulo": mapa_original.get("titulo", "Mapa Conceptual"),
        "conceptos_principales": conceptos_principales,
        "conceptos_secundarios": conceptos_secundarios,
        "conceptos_terciarios": conceptos_terciarios,  # ✅ NUEVO: mantener terciarios
        "relaciones": relaciones,
        "estructura": {
            "total_conceptos": len(conceptos),
            "niveles": len(set([c.get("nivel", "") for c in conceptos])),
            "profundidad_maxima": max([c.get("id", "").count("_") for c in conceptos]) + 1
        }
    }
    
    print(f"✅ Estructura normalizada: {len(conceptos_principales)} principales, {len(conceptos_secundarios)} secundarios, {len(conceptos_terciarios)} terciarios")
    return mapa_normalizado
def procesar_mapa_conceptual_api(mapa_texto, solo_validar=False):
    """
    Función centralizada para procesar la respuesta de la API del mapa conceptual
    Ahora con mejor manejo de errores
    """
    try:
        if not solo_validar:
            print("🧩 MENSAJE COMPLETO DE LA API EXTERNA ↓↓↓↓↓↓↓↓↓↓↓")
            print(mapa_texto)
            print("↑↑↑↑↑↑↑↑↑↑ FIN DEL MENSAJE COMPLETO")

        mapa_texto_limpio = mapa_texto.strip()
        
        if not mapa_texto_limpio:
            raise ValueError("Mapa conceptual vacío")

        json_str = None

        # Extraer JSON de la respuesta
        if mapa_texto_limpio.startswith('{'):
            json_str = mapa_texto_limpio
            if not solo_validar:
                print("✅ JSON directo (sin marcas de código)")
        else:
            match = re.search(r'```json\s*([\s\S]*?)\s*```', mapa_texto)
            if match:
                json_str = match.group(1).strip()
                if not solo_validar:
                    print("✅ JSON encontrado en bloque ```json")
            else:
                match_json = re.search(r'\{[\s\S]*\}', mapa_texto_limpio)
                if match_json:
                    json_str = match_json.group(0)
                    if not solo_validar:
                        print("✅ JSON extraído del texto (patrón general)")

        if not json_str:
            raise ValueError("No se encontró JSON válido en la respuesta")

        # Limpiar y parsear JSON
        json_limpio = limpiar_json(json_str)
        mapa_json = json.loads(json_limpio)
        
        if not solo_validar:
            print("✅ JSON parseado correctamente después de limpiar")
            # Normalizar y limpiar para Mermaid
            return limpiar_mapa_para_mermaid(mapa_json)
        else:
            # Solo retornar el JSON parseado para validación
            return mapa_json
            
    except json.JSONDecodeError as e:
        raise ValueError(f"Error decodificando JSON: {str(e)}")
    except Exception as e:
        raise ValueError(f"Error procesando mapa: {str(e)}")
    


def validar_estructura_arbol(conceptos, relaciones, ids_conceptos):
    """
    Valida que la estructura del árbol sea lógica:
    - No hay ciclos
    - No hay conceptos huérfanos (excepto el raíz)
    - La jerarquía es coherente
    """
    problemas = []
    
    # ✅ CONSTRUIR GRAFO SOLO CON RELACIONES VÁLIDAS
    grafo = {id_concepto: [] for id_concepto in ids_conceptos}
    relaciones_invalidas_en_arbol = []
    
    for relacion in relaciones:
        origen = relacion.get("origen")
        destino = relacion.get("destino")
        
        # ✅ VERIFICAR SI LA RELACIÓN ES VÁLIDA
        if origen in ids_conceptos and destino in ids_conceptos:
            grafo[origen].append(destino)
        else:
            # Registrar relación inválida para debug
            if origen not in ids_conceptos:
                relaciones_invalidas_en_arbol.append(f"Origen '{origen}' no existe")
            if destino not in ids_conceptos:
                relaciones_invalidas_en_arbol.append(f"Destino '{destino}' no existe")
    
    if relaciones_invalidas_en_arbol:
        problemas.extend(relaciones_invalidas_en_arbol)
    
    # Encontrar concepto raíz
    raiz = None
    for concepto in conceptos:
        if concepto.get("nivel") == "raiz" or concepto.get("id") == "cp_1":
            raiz = concepto.get("id")
            break
    
    if not raiz:
        problemas.append("No se pudo identificar el concepto raíz")
        return problemas
    
    # ✅ SOLO VERIFICAR CICLOS SI HAY RELACIONES VÁLIDAS
    if any(grafo.values()):  # Solo si hay al menos una relación válida
        if detectar_ciclos(grafo, raiz):
            problemas.append("Se detectaron ciclos en la estructura del mapa")
    
    # ✅ VERIFICAR CONCEPTOS HUÉRFANOS (excluyendo el raíz)
    if any(grafo.values()):  # Solo si hay relaciones
        todos_destinos = set()
        for destinos in grafo.values():
            todos_destinos.update(destinos)
        
        conceptos_conectados = {raiz} | todos_destinos
        conceptos_huérfanos = ids_conceptos - conceptos_conectados
        
        # ✅ EXCLUIR EL RAÍZ DE LOS HUÉRFANOS (es normal que no tenga padre)
        if raiz in conceptos_huérfanos:
            conceptos_huérfanos.remove(raiz)
            
        if conceptos_huérfanos:
            problemas.append(f"Conceptos huérfanos (sin conexión al árbol): {', '.join(conceptos_huérfanos)}")
    else:
        # Si no hay relaciones válidas, todos los conceptos excepto el raíz están huérfanos
        conceptos_huérfanos = ids_conceptos - {raiz}
        if conceptos_huérfanos:
            problemas.append(f"Sin relaciones válidas - conceptos desconectados: {', '.join(conceptos_huérfanos)}")
    
    return problemas
                

def detectar_ciclos(grafo, inicio):
    """Detecta ciclos en el grafo usando DFS"""
    visitados = set()
    en_camino = set()
    
    def dfs(nodo):
        if nodo in en_camino:
            return True
        if nodo in visitados:
            return False
            
        visitados.add(nodo)
        en_camino.add(nodo)
        
        for vecino in grafo.get(nodo, []):
            if dfs(vecino):
                return True
                
        en_camino.remove(nodo)
        return False
    
    return dfs(inicio)


def validar_profundidad_mapa(conceptos, relaciones):
    """
    Valida que el mapa tenga suficiente profundidad conceptual
    Versión MEJORADA - más flexible
    """
    if len(conceptos) < 3:
        return False  # Mínimo raíz + 2 conceptos
    
    # Contar niveles de profundidad por estructura de IDs
    niveles = set()
    for concepto in conceptos:
        id_concepto = concepto.get("id", "")
        # Estimar nivel por el ID (cp_1, cp_1_1, cp_1_1_1, etc.)
        nivel = id_concepto.count("_")
        niveles.add(nivel)
    
    # Debe tener al menos 2 niveles de profundidad (raíz + secundarios)
    # O si tiene muchos conceptos, puede ser más plano pero informativo
    return len(niveles) >= 2 or len(conceptos) >= 5


@app.get("/planificacion/{guion_id}/mapa-conceptual")
async def generar_mapa_conceptual(
    guion_id: int, 
    accion: str = Query("obtener", description="Acción: 'obtener' (default), 'regenerar'")
):
    print(f"🗺️ Mapa conceptual - Guión: {guion_id}, Acción: {accion}")
    thread_id = None
    conn = None
    cursor = None

    try:
        # 1. Conectar a BD
        conn = connect_db()
        cursor = conn.cursor()

        
        # 2. Verificar si ya existe mapa y no se quiere regenerar
        if accion == "obtener":
            cursor.execute("""
                SELECT titulo, conceptos, relaciones, metadata
                FROM mapa_conceptual 
                WHERE id_guion_clase = %s 
                ORDER BY version DESC 
                LIMIT 1
            """, (guion_id,))
            
            mapa_existente = cursor.fetchone()
            if mapa_existente:
                print("✅ Mapa conceptual encontrado en BD, devolviendo...")
                
                # Parsear JSON de la BD
                conceptos = json.loads(mapa_existente['conceptos']) if mapa_existente['conceptos'] else []
                relaciones = json.loads(mapa_existente['relaciones']) if mapa_existente['relaciones'] else []
                metadata = json.loads(mapa_existente['metadata']) if mapa_existente['metadata'] else {}
                
                # Formatear mapa_procesado
                mapa_procesado = {
                    "titulo": mapa_existente.get('titulo', ''),
                    "conceptos": conceptos,
                    "relaciones": relaciones
                }
                
                # Retornar EXACTAMENTE igual que antes
                return {
                    "mapa_conceptual": mapa_procesado,
                    "unidad_nombre": metadata.get("unidad_nombre"),
                    "profesor": metadata.get("profesor"),
                    "titulo_guion": metadata.get("titulo_guion", ""),
                    "nombre_asignatura": metadata.get("nombre_asignatura", "Asignatura no especificada"),
                    "nombre_curso": metadata.get("nombre_curso", "Curso no especificado")
                }
        
        # 3. Si no existe o se quiere regenerar, obtener información del guión
        print(f"🔍 Obteniendo información del guión {guion_id}")
        cursor.execute("""
            SELECT 
                g.vector_id, 
                g.assistant_id, 
                g.titulo, 
                u.nombre AS unidad_nombre, 
                usr.nombre AS profesor,
                c.nombre AS nombre_curso,
                p.identificacion_clase
            FROM guion_clase g
            JOIN unidad u ON g.id_unidad = u.id
            JOIN curso c ON u.id_curso = c.id
            LEFT JOIN planificacion p ON p.id_guion_clase = g.id
            LEFT JOIN usuario_curso uc ON uc.id_curso = c.id
            LEFT JOIN usuario usr ON uc.id_usuario = usr.id
            WHERE g.id = %s AND usr.tipo = 2
            LIMIT 1
        """, (guion_id,))
        
        result = cursor.fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail=f"Guion {guion_id} no encontrado")
            
        # ✅ Verificar recursos ANTES de crear thread
        recursos_ok, mensaje_recursos = await verificar_recursos_antes_de_procesar(
            result['vector_id'], 
            result['assistant_id']
        )
        if not recursos_ok:
            raise HTTPException(status_code=400, detail=mensaje_recursos)
            
        # Guardar result para usarlo después
        result_data = {
            'vector_id': result['vector_id'],
            'assistant_id': result['assistant_id'],
            'titulo': result['titulo'],
            'unidad_nombre': result['unidad_nombre'],
            'profesor': result['profesor'],
            'nombre_curso': result['nombre_curso'],
            'identificacion_clase': result['identificacion_clase']
        }
            
    except Exception as db_error:
        print(f"❌ Error en base de datos: {db_error}")
        raise HTTPException(status_code=500, detail=f"Error accediendo a datos: {str(db_error)}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    # GUARDAR EN LA BASE DE DATOS
  
    try:
        # ✅ Throttling global ANTES de crear thread
        await throttling_global()
        
        # ✅ Crear thread y llamar API
        nuevo_thread = client.beta.threads.create()
        thread_id = nuevo_thread.id
        print(f"🆕 Nuevo thread creado para mapa: {thread_id}")
        
        url = f"{url_api}/generar_mapa_conceptual/{result_data['assistant_id']}"
        data = {
            "thread_id": thread_id,
            "vector_id": result_data['vector_id'],
            "titulo_guion": result_data['titulo']
        }
        
        # ✅ Llamar a la API
        data_response = await llamar_api_con_reintentos_y_cancelacion(
            url, data, max_intentos=3, tipo_contenido="mapa_conceptual"
        )
        
        if not data_response:
            raise HTTPException(
                status_code=500, 
                detail="No se pudo generar un mapa conceptual válido después de 3 intentos"
            )
        
        # Procesar respuesta exitosa
        mapa_texto = data_response.get("mapa_conceptual", "")
        mapa_procesado = procesar_mapa_conceptual_api(mapa_texto)
        
        # Extraer nombre_asignatura
        nombre_asignatura = "Asignatura no especificada"
        if result_data.get('identificacion_clase'):
            try:
                identificacion_data = json.loads(result_data['identificacion_clase'])
                nombre_asignatura = identificacion_data.get('nombre_asignatura', result_data.get('nombre_curso', 'Asignatura no especificada'))
            except:
                nombre_asignatura = result_data.get('nombre_curso', 'Asignatura no especificada')
        else:
            nombre_asignatura = result_data.get('nombre_curso', 'Asignatura no especificada')
        
        # GUARDAR EN LA BASE DE DATOS
        conn = connect_db()
        cursor = conn.cursor()
        
        # Obtener número de versión
        cursor.execute("""
            SELECT COALESCE(MAX(version), 0) + 1 as nueva_version
            FROM mapa_conceptual 
            WHERE id_guion_clase = %s
        """, (guion_id,))
        
        version_result = cursor.fetchone()
        nueva_version = version_result[0] if version_result else 1
        
        # Preparar metadata para guardar
        metadata = {
            "unidad_nombre": result_data.get("unidad_nombre"),
            "profesor": result_data.get("profesor"),
            "titulo_guion": result_data.get("titulo", ""),
            "nombre_asignatura": nombre_asignatura,
            "nombre_curso": result_data.get("nombre_curso", "Curso no especificado"),
            "vector_id": result_data.get("vector_id"),
            "assistant_id": result_data.get("assistant_id")
        }
        
        # BORRAR MAPAS CONCEPTUALES ANTERIORES DEL MISMO GUION
        cursor.execute("""
            DELETE FROM mapa_conceptual 
            WHERE id_guion_clase = %s
        """, (guion_id,))

        # Insertar nuevo mapa (SIEMPRE con version = 1)
        cursor.execute("""
            INSERT INTO mapa_conceptual (
                id_guion_clase, 
                titulo, 
                conceptos,
                relaciones, 
                metadata,
                version
            ) VALUES (%s, %s, %s, %s, %s, 1)
        """, (
            guion_id,
            mapa_procesado.get("titulo", ""),
            json.dumps(mapa_procesado.get("conceptos", [])),
            json.dumps(mapa_procesado.get("relaciones", [])),
            json.dumps(metadata)
        ))
        conn.commit()
        cursor.close()
        conn.close()
        print(f"✅ Mapa conceptual guardado en BD (v{nueva_version})")
        
        # RETORNAR EXACTAMENTE IGUAL
        return {
            "mapa_conceptual": mapa_procesado,
            "unidad_nombre": result_data.get("unidad_nombre"),
            "profesor": result_data.get("profesor"),
            "titulo_guion": result_data.get("titulo"),
            "nombre_asignatura": nombre_asignatura,
            "nombre_curso": result_data.get("nombre_curso", "Curso no especificado")
        }
            
    except HTTPException:
        raise
    except Exception as api_error:
        print(f"❌ Error en generación de mapa: {api_error}")
        raise HTTPException(status_code=500, detail=f"Error generando mapa conceptual: {str(api_error)}")
        
    finally:
        # ✅ Limpiar thread
        if thread_id:
            try:
                client.beta.threads.delete(thread_id)
                print(f"🧹 Thread {thread_id} eliminado")
            except Exception as e:
                print(f"⚠️ No se pudo eliminar thread: {e}")
        


@app.post("/planificacion/{guion_id}/mapa-conceptual/regenerar")
async def regenerar_mapa_conceptual(guion_id: int):
    """Endpoint específico para regenerar el mapa conceptual"""
    return await generar_mapa_conceptual(guion_id, "regenerar")


@app.get("/planificacion/{guion_id}/mapa-conceptual/existe")
async def verificar_mapa_conceptual_existe(guion_id: int):
    """Verifica si ya existe un mapa conceptual para este guión"""
    try:
        conn = connect_db()
        cursor = conn.cursor()

        
        cursor.execute("""
            SELECT id, version, creado_en, titulo 
            FROM mapa_conceptual 
            WHERE id_guion_clase = %s 
            ORDER BY version DESC 
            LIMIT 1
        """, (guion_id,))
        
        mapa = cursor.fetchone()
        
        return {
            "existe": mapa is not None,
            "version": mapa['version'] if mapa else 0,
            "titulo": mapa['titulo'] if mapa else "",
            "creado_en": mapa['creado_en'].isoformat() if mapa and mapa['creado_en'] else None
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
#################################################################
#################################################################
#################################################################


#########################################################################
##################
################## Funcion para obtener flashcards
##################
#########################################################################
@app.get("/planificacion/{guion_id}/flashcards")
async def generar_flashcards(
    guion_id: int, 
    accion: str = Query("obtener", description="Acción: 'obtener' (default), 'regenerar'")
):
    print(f"📚 Flashcards - Guión: {guion_id}, Acción: {accion}")
    thread_id = None
    conn = None
    cursor = None

    try:
        # 1. Conectar a BD
        conn = connect_db()
        cursor = conn.cursor()

        
        # 2. Verificar si ya existen flashcards y no se quiere regenerar
        if accion == "obtener":
            cursor.execute("""
                SELECT cards, metadata
                FROM flashcard 
                WHERE id_guion_clase = %s 
                ORDER BY version DESC 
                LIMIT 1
            """, (guion_id,))
            
            flashcards_existentes = cursor.fetchone()
            if flashcards_existentes:
                print("✅ Flashcards encontradas en BD, devolviendo...")
                
                # Parsear JSON de la BD
                cards = json.loads(flashcards_existentes['cards']) if flashcards_existentes['cards'] else []
                metadata = json.loads(flashcards_existentes['metadata']) if flashcards_existentes['metadata'] else {}
                total_cards = len(cards)
                
                # Formatear flashcards
                flashcards_formateadas = []
                for i, card in enumerate(cards):
                    if isinstance(card, dict):
                        flashcards_formateadas.append({
                            "id": i + 1,
                            "pregunta": card.get("pregunta", f"Pregunta {i+1}"),
                            "respuesta": card.get("respuesta", f"Respuesta {i+1}"),
                            "categoria": card.get("categoria", "concepto")
                        })
                
                # Retornar EXACTAMENTE igual que antes
                return JSONResponse({
                    "flashcards": flashcards_formateadas,
                    "unidad_nombre": metadata.get("unidad_nombre", "Sin nombre"),
                    "profesor": metadata.get("profesor", "Docente no especificado"),
                    "nombre_curso": metadata.get("nombre_curso", "Curso no especificado"),
                    "nombre_asignatura": metadata.get("nombre_asignatura", "Asignatura no especificada"),
                    "nombre_unidad": metadata.get("nombre_unidad", "Unidad no especificada"),
                    "total_cards": total_cards
                })
        
        # 3. Si no existen o se quiere regenerar, obtener información del guión
        print(f"🔍 Obteniendo información del guión {guion_id}")
        cursor.execute("""
            SELECT 
                g.vector_id, 
                g.assistant_id, 
                u.nombre AS unidad_nombre, 
                usr.nombre AS profesor,
                c.nombre AS nombre_curso,
                p.identificacion_clase
            FROM guion_clase g
            JOIN unidad u ON g.id_unidad = u.id
            JOIN curso c ON u.id_curso = c.id
            LEFT JOIN planificacion p ON p.id_guion_clase = g.id
            LEFT JOIN usuario_curso uc ON uc.id_curso = c.id
            LEFT JOIN usuario usr ON uc.id_usuario = usr.id
            WHERE g.id = %s AND usr.tipo = 2
            LIMIT 1
        """, (guion_id,))
        
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Guion no encontrado")
            
        # ✅ Verificar recursos ANTES de crear thread
        recursos_ok, mensaje_recursos = await verificar_recursos_antes_de_procesar(
            result['vector_id'], 
            result['assistant_id']
        )
        if not recursos_ok:
            raise HTTPException(status_code=400, detail=mensaje_recursos)
            
        # Guardar result para usarlo después
        result_data = {
            'vector_id': result['vector_id'],
            'assistant_id': result['assistant_id'],
            'unidad_nombre': result['unidad_nombre'],
            'profesor': result['profesor'],
            'nombre_curso': result['nombre_curso'],
            'identificacion_clase': result['identificacion_clase']
        }
            
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    try:
        # ✅ Throttling global ANTES de crear thread
        await throttling_global()
        
        # Crear thread
        nuevo_thread = client.beta.threads.create()
        thread_id = nuevo_thread.id
        print(f"🆕 Nuevo thread creado para flashcards: {thread_id}")
        
        url = f"{url_api}/generar_flashcards/{result_data['assistant_id']}"
        data = {"thread_id": thread_id, "vector_id": result_data['vector_id']}
        
        # ✅ Llamar a la API
        data_response = await llamar_api_con_reintentos_y_cancelacion(
            url, data, max_intentos=3, tipo_contenido="flashcards"
        )
        
        # ✅ MANTENER: Tu lógica de procesamiento actual
        if data_response:
            flashcards_texto = data_response.get("flashcards", "")
        else:
            flashcards_texto = ""

        # Tu lógica actual de procesamiento de flashcards...
        match = re.search(r'```json([\s\S]*?)```', flashcards_texto)
        if match:
            try:
                flashcards_json = json.loads(match.group(1))
            except json.JSONDecodeError:
                flashcards_json = {"error": "No se pudieron generar las flashcards"}
        else:
            try:
                flashcards_json = json.loads(flashcards_texto)
            except json.JSONDecodeError:
                flashcards_json = {"error": "No se pudieron generar las flashcards"}

        # Asegurar que tenemos un array de flashcards
        if isinstance(flashcards_json, dict) and "flashcards" in flashcards_json:
            flashcards_array = flashcards_json["flashcards"]
        elif isinstance(flashcards_json, list):
            flashcards_array = flashcards_json
        else:
            flashcards_array = []

        # Validar y formatear las flashcards
        flashcards_formateadas = []
        for i, card in enumerate(flashcards_array):
            if isinstance(card, dict):
                flashcards_formateadas.append({
                    "id": i + 1,
                    "pregunta": card.get("pregunta", f"Pregunta {i+1}"),
                    "respuesta": card.get("respuesta", f"Respuesta {i+1}"),
                    "categoria": card.get("categoria", "concepto")
                })

        # Si no se generaron flashcards válidas, crear algunas de ejemplo
        if not flashcards_formateadas:
            print("⚠️ No se generaron flashcards válidas, creando ejemplo...")
            flashcards_formateadas = [
                {
                    "id": 1,
                    "pregunta": "Ejemplo de pregunta sobre el contenido",
                    "respuesta": "Ejemplo de respuesta explicativa",
                    "categoria": "concepto"
                }
            ]
        
        # Extraer nombre_asignatura
        nombre_asignatura = "Asignatura no especificada"
        if result_data.get('identificacion_clase'):
            try:
                identificacion_data = json.loads(result_data['identificacion_clase'])
                nombre_asignatura = identificacion_data.get('nombre_asignatura', result_data.get('nombre_curso', 'Asignatura no especificada'))
            except:
                nombre_asignatura = result_data.get('nombre_curso', 'Asignatura no especificada')
        else:
            nombre_asignatura = result_data.get('nombre_curso', 'Asignatura no especificada')
        
        # GUARDAR EN LA BASE DE DATOS
        conn = connect_db()
        cursor = conn.cursor()
        
        # Obtener número de versión
        cursor.execute("""
            SELECT COALESCE(MAX(version), 0) + 1 as nueva_version
            FROM flashcard 
            WHERE id_guion_clase = %s
        """, (guion_id,))
        
        version_result = cursor.fetchone()
        nueva_version = version_result[0] if version_result else 1
        
        # Preparar metadata para guardar
        metadata = {
            "unidad_nombre": result_data.get("unidad_nombre", "Sin nombre"),
            "profesor": result_data.get("profesor", "Docente no especificado"),
            "nombre_curso": result_data.get("nombre_curso", "Curso no especificado"),
            "nombre_asignatura": nombre_asignatura,
            "nombre_unidad": result_data.get("unidad_nombre", "Unidad no especificada"),
            "vector_id": result_data.get("vector_id"),
            "assistant_id": result_data.get("assistant_id")
        }
        
        total_cards = len(flashcards_formateadas)
        
        # BORRAR FLASHCARDS ANTERIORES DEL MISMO GUION
        cursor.execute("""
            DELETE FROM flashcard 
            WHERE id_guion_clase = %s
        """, (guion_id,))

        # Insertar nuevas flashcards (SIEMPRE con version = 1)
        cursor.execute("""
            INSERT INTO flashcard (
                id_guion_clase, 
                cards, 
                metadata,
                version
            ) VALUES (%s, %s, %s, 1)
        """, (
            guion_id,
            json.dumps(flashcards_formateadas),
            json.dumps(metadata)
        ))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print(f"✅ Flashcards guardadas en BD (v{nueva_version}) - {total_cards} cards")
        
        # RETORNAR EXACTAMENTE IGUAL
        return JSONResponse({
            "flashcards": flashcards_formateadas,
            "unidad_nombre": result_data.get("unidad_nombre", "Sin nombre"),
            "profesor": result_data.get("profesor", "Docente no especificado"),
            "nombre_curso": result_data.get("nombre_curso", "Curso no especificado"),
            "nombre_asignatura": nombre_asignatura,
            "nombre_unidad": result_data.get("unidad_nombre", "Unidad no especificada"),
            "total_cards": total_cards
        })

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error en generación de flashcards: {e}")
        raise HTTPException(status_code=500, detail="Error generando flashcards")
        
    finally:
        # Limpiar thread
        if thread_id:
            try:
                client.beta.threads.delete(thread_id)
                print(f"🧹 Thread {thread_id} eliminado (flashcards)")
            except Exception as e:
                print(f"⚠️ No se pudo eliminar thread: {e}")


@app.post("/planificacion/{guion_id}/flashcards/regenerar")
async def regenerar_flashcards(guion_id: int):
    """Endpoint específico para regenerar las flashcards"""
    return await generar_flashcards(guion_id, "regenerar")


@app.get("/planificacion/{guion_id}/flashcards/existe")
async def verificar_flashcards_existe(guion_id: int):
    """Verifica si ya existen flashcards para este guión"""
    try:
        conn = connect_db()
        cursor = conn.cursor()

        
        cursor.execute("""
            SELECT id, version, creado_en, 
            FROM flashcard 
            WHERE id_guion_clase = %s 
            ORDER BY version DESC 
            LIMIT 1
        """, (guion_id,))
        
        flashcards = cursor.fetchone()
        
        return {
            "existe": flashcards is not None,
            "version": flashcards['version'] if flashcards else 0,
            "creado_en": flashcards['creado_en'].isoformat() if flashcards and flashcards['creado_en'] else None
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

#################################################################
#################################################################
#################################################################

#########################################################################
##################
################## Funcion para obtener glosario
##################
#########################################################################
@app.get("/planificacion/{guion_id}/glosario")
async def generar_glosario(
    guion_id: int, 
    accion: str = Query("obtener", description="Acción: 'obtener' (default), 'regenerar'")
):
    print(f"📖 Glosario - Guión: {guion_id}, Acción: {accion}")
    thread_id = None
    conn = None
    cursor = None

    try:
        # 1. Conectar a BD
        conn = connect_db()
        cursor = conn.cursor()

        
        # 2. Verificar si ya existe glosario y no se quiere regenerar
        if accion == "obtener":
            cursor.execute("""
                SELECT terminos, metadata
                FROM glosario 
                WHERE id_guion_clase = %s 
                ORDER BY version DESC 
                LIMIT 1
            """, (guion_id,))
            
            glosario_existente = cursor.fetchone()
            if glosario_existente:
                print("✅ Glosario encontrado en BD, devolviendo...")
                
                # Parsear JSON de la BD
                terminos = json.loads(glosario_existente['terminos']) if glosario_existente['terminos'] else []
                metadata = json.loads(glosario_existente['metadata']) if glosario_existente['metadata'] else {}
                total_terminos = len(terminos)
                
                # Formatear glosario
                glosario_formateado = []
                for i, termino in enumerate(terminos):
                    if isinstance(termino, dict):
                        glosario_formateado.append({
                            "id": i + 1,
                            "termino": termino.get("termino", f"Término {i+1}"),
                            "definicion": termino.get("definicion", f"Definición del término {i+1}"),
                            "categoria": termino.get("categoria", "general"),
                            "ejemplo": termino.get("ejemplo", "")
                        })
                
                # Retornar EXACTAMENTE igual que antes
                return JSONResponse({
                    "glosario": glosario_formateado,
                    "unidad_nombre": metadata.get("unidad_nombre", "Sin nombre"),
                    "profesor": metadata.get("profesor", "Docente no especificado"),
                    "nombre_curso": metadata.get("nombre_curso", "Curso no especificado"),
                    "nombre_asignatura": metadata.get("nombre_asignatura", "Asignatura no especificada"),
                    "total_terminos": total_terminos
                })
        
        # 3. Si no existe o se quiere regenerar, obtener información del guión
        print(f"🔍 Obteniendo información del guión {guion_id}")
        cursor.execute("""
            SELECT 
                g.vector_id, 
                g.assistant_id, 
                u.nombre AS unidad_nombre, 
                usr.nombre AS profesor,
                c.nombre AS nombre_curso,
                p.identificacion_clase
            FROM guion_clase g
            JOIN unidad u ON g.id_unidad = u.id
            JOIN curso c ON u.id_curso = c.id
            LEFT JOIN planificacion p ON p.id_guion_clase = g.id
            LEFT JOIN usuario_curso uc ON uc.id_curso = c.id
            LEFT JOIN usuario usr ON uc.id_usuario = usr.id
            WHERE g.id = %s AND usr.tipo = 2
            LIMIT 1
        """, (guion_id,))
        
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Guion no encontrado")
            
        # ✅ Verificar recursos ANTES de crear thread
        recursos_ok, mensaje_recursos = await verificar_recursos_antes_de_procesar(
            result['vector_id'], 
            result['assistant_id']
        )
        if not recursos_ok:
            raise HTTPException(status_code=400, detail=mensaje_recursos)
            
        # Guardar result para usarlo después
        result_data = {
            'vector_id': result['vector_id'],
            'assistant_id': result['assistant_id'],
            'unidad_nombre': result['unidad_nombre'],
            'profesor': result['profesor'],
            'nombre_curso': result['nombre_curso'],
            'identificacion_clase': result['identificacion_clase']
        }
            
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    try:
        # ✅ Throttling global ANTES de crear thread
        await throttling_global()
        
        # Crear thread
        nuevo_thread = client.beta.threads.create()
        thread_id = nuevo_thread.id
        print(f"🆕 Nuevo thread creado para glosario: {thread_id}")
        
        url = f"{url_api}/generar_glosario/{result_data['assistant_id']}"
        data = {"thread_id": thread_id, "vector_id": result_data['vector_id']}
        
        # ✅ Llamar a la API
        data_response = await llamar_api_con_reintentos_y_cancelacion(
            url, data, max_intentos=3, tipo_contenido="glosario"
        )
        
        # ✅ MANTENER: Tu lógica de procesamiento actual
        if data_response:
            glosario_texto = data_response.get("glosario", "")
        else:
            glosario_texto = ""

        # Tu lógica actual de procesamiento de glosario...
        match = re.search(r'```json([\s\S]*?)```', glosario_texto)
        if match:
            try:
                glosario_json = json.loads(match.group(1))
            except json.JSONDecodeError:
                glosario_json = {"error": "No se pudo generar el glosario"}
        else:
            try:
                glosario_json = json.loads(glosario_texto)
            except json.JSONDecodeError:
                glosario_json = {"error": "No se pudo generar el glosario"}

        # Asegurar que tenemos un array de términos del glosario
        if isinstance(glosario_json, dict) and "glosario" in glosario_json:
            glosario_array = glosario_json["glosario"]
        elif isinstance(glosario_json, list):
            glosario_array = glosario_json
        else:
            glosario_array = []

        # Validar y formatear los términos del glosario
        glosario_formateado = []
        for i, termino in enumerate(glosario_array):
            if isinstance(termino, dict):
                glosario_formateado.append({
                    "id": i + 1,
                    "termino": termino.get("termino", f"Término {i+1}"),
                    "definicion": termino.get("definicion", f"Definición del término {i+1}"),
                    "categoria": termino.get("categoria", "general"),
                    "ejemplo": termino.get("ejemplo", "")
                })

        # Si no se generó glosario válido, crear ejemplo
        if not glosario_formateado:
            print("⚠️ No se generó glosario válido, crear ejemplo...")
            glosario_formateado = [
                {
                    "id": 1,
                    "termino": "Concepto Principal",
                    "definicion": "Definición fundamental del concepto clave del contenido",
                    "categoria": "concepto",
                    "ejemplo": "Ejemplo práctico de aplicación del concepto"
                }
            ]
        
        # Extraer nombre_asignatura
        nombre_asignatura = "Asignatura no especificada"
        if result_data.get('identificacion_clase'):
            try:
                identificacion_data = json.loads(result_data['identificacion_clase'])
                nombre_asignatura = identificacion_data.get('nombre_asignatura', result_data.get('nombre_curso', 'Asignatura no especificada'))
            except:
                nombre_asignatura = result_data.get('nombre_curso', 'Asignatura no especificada')
        else:
            nombre_asignatura = result_data.get('nombre_curso', 'Asignatura no especificada')
        
        # GUARDAR EN LA BASE DE DATOS
        conn = connect_db()
        cursor = conn.cursor()
        
        # Obtener número de versión
        cursor.execute("""
            SELECT COALESCE(MAX(version), 0) + 1 as nueva_version
            FROM glosario 
            WHERE id_guion_clase = %s
        """, (guion_id,))
        
        version_result = cursor.fetchone()
        nueva_version = version_result[0] if version_result else 1
        
        # Preparar metadata para guardar
        metadata = {
            "unidad_nombre": result_data.get("unidad_nombre", "Sin nombre"),
            "profesor": result_data.get("profesor", "Docente no especificado"),
            "nombre_curso": result_data.get("nombre_curso", "Curso no especificado"),
            "nombre_asignatura": nombre_asignatura,
            "nombre_unidad": result_data.get("unidad_nombre", "Unidad no especificada"),
            "vector_id": result_data.get("vector_id"),
            "assistant_id": result_data.get("assistant_id")
        }
        
        total_terminos = len(glosario_formateado)
        
        # BORRAR GLOSARIOS ANTERIORES DEL MISMO GUION
        cursor.execute("""
            DELETE FROM glosario 
            WHERE id_guion_clase = %s
        """, (guion_id,))

        # Insertar nuevo glosario (SIEMPRE con version = 1)
        cursor.execute("""
            INSERT INTO glosario (
                id_guion_clase, 
                terminos, 
                metadata,
                version
            ) VALUES (%s, %s, %s, 1)
        """, (
            guion_id,
            json.dumps(glosario_formateado),
            json.dumps(metadata)
        ))
                
        conn.commit()
        cursor.close()
        conn.close()
        
        print(f"✅ Glosario guardado en BD (v{nueva_version}) - {total_terminos} términos")
        
        # RETORNAR EXACTAMENTE IGUAL
        return JSONResponse({
            "glosario": glosario_formateado,
            "unidad_nombre": result_data.get("unidad_nombre", "Sin nombre"),
            "profesor": result_data.get("profesor", "Docente no especificado"),
            "nombre_curso": result_data.get("nombre_curso", "Curso no especificado"),
            "nombre_asignatura": nombre_asignatura,
            "total_terminos": total_terminos
        })

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error en generación de glosario: {e}")
        raise HTTPException(status_code=500, detail="Error generando glosario")
        
    finally:
        # Limpiar thread
        if thread_id:
            try:
                client.beta.threads.delete(thread_id)
                print(f"🧹 Thread {thread_id} eliminado (glosario)")
            except Exception as e:
                print(f"⚠️ No se pudo eliminar thread: {e}")


@app.post("/planificacion/{guion_id}/glosario/regenerar")
async def regenerar_glosario(guion_id: int):
    """Endpoint específico para regenerar el glosario"""
    return await generar_glosario(guion_id, "regenerar")


@app.get("/planificacion/{guion_id}/glosario/existe")
async def verificar_glosario_existe(guion_id: int):
    """Verifica si ya existe un glosario para este guión"""
    try:
        conn = connect_db()
        cursor = conn.cursor()

        
        cursor.execute("""
            SELECT id, version, creado_en,
            FROM glosario 
            WHERE id_guion_clase = %s 
            ORDER BY version DESC 
            LIMIT 1
        """, (guion_id,))
        
        glosario = cursor.fetchone()
        
        return {
            "existe": glosario is not None,
            "version": glosario['version'] if glosario else 0,
            "creado_en": glosario['creado_en'].isoformat() if glosario and glosario['creado_en'] else None
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()



#################################################################
#################################################################
#################################################################

#########################################################################
##################
################## Funcion para obtener infografia
##################
#########################################################################
@app.get("/planificacion/{guion_id}/infografia")
async def generar_infografia(
    guion_id: int, 
    accion: str = Query("obtener", description="Acción: 'obtener' (default), 'regenerar'")
):
    print(f"🎨 Infografía - Guión: {guion_id}, Acción: {accion}")
    
    thread_id = None
    conn = None
    cursor = None

    try:
        # 1. Conectar a BD
        conn = connect_db()
        cursor = conn.cursor()

        
        # 2. Verificar si ya existe infografía y no se quiere regenerar
        if accion == "obtener":
            cursor.execute("""
                SELECT imagen_url, imagen_base64, metadata, titulo 
                FROM infografia 
                WHERE id_guion_clase = %s 
                ORDER BY version DESC 
                LIMIT 1
            """, (guion_id,))
            
            infografia_existente = cursor.fetchone()
            if infografia_existente:
                print("✅ Infografía encontrada en BD, verificando...")
                
                # Parsear metadata
                metadata = json.loads(infografia_existente['metadata']) if infografia_existente['metadata'] else {}
                
                # Obtener la imagen
                imagen_base64 = None
                
                # 2.1 Si ya tenemos base64 guardado (completo o parcial)
                if infografia_existente['imagen_base64'] and len(infografia_existente['imagen_base64']) > 100:
                    imagen_base64 = infografia_existente['imagen_base64']
                    print(f"📁 Imagen encontrada en BD (base64): {len(imagen_base64)} chars")
                
                # 2.2 Si no, pero tenemos URL de Banana, descargarla
                elif infografia_existente['imagen_url'] and infografia_existente['imagen_url'].startswith('http'):
                    try:
                        print(f"🌐 Descargando imagen desde URL: {infografia_existente['imagen_url']}")
                        imagen_base64 = await _download_image(infografia_existente['imagen_url'])
                        if imagen_base64:
                            print(f"✅ Imagen descargada: {len(imagen_base64)} chars")
                    except Exception as e:
                        print(f"⚠️ Error descargando imagen: {e}")
                        imagen_base64 = None
                
                if imagen_base64:
                    # Retornar EXACTAMENTE igual que antes
                    return JSONResponse({
                        "infografia": imagen_base64,
                        "titulo": infografia_existente.get('titulo', ''),
                        "guion_id": guion_id,
                        "unidad_nombre": metadata.get("unidad_nombre", "Sin nombre"),
                        "profesor": metadata.get("profesor", "Docente no especificado"),
                        "nombre_curso": metadata.get("nombre_curso", "Curso no especificado"),
                        "nombre_asignatura": metadata.get("nombre_asignatura", "Asignatura no especificada"),
                        "nombre_unidad": metadata.get("nombre_unidad", "Unidad no especificada")
                    })
                else:
                    print("⚠️ Infografía en BD pero no se pudo obtener imagen")
        
        # 3. Si no existe o se quiere regenerar, obtener información del guión
        print(f"🔍 Obteniendo información del guión {guion_id}")
        cursor.execute("""
            SELECT 
                g.titulo,
                g.ra AS recursos_aprendizaje,
                g.contenido AS contenidos, 
                g.vector_id,
                g.assistant_id,
                u.nombre AS unidad_nombre,
                usr.nombre AS profesor,
                c.nombre AS nombre_curso,
                p.identificacion_clase
            FROM guion_clase g
            JOIN unidad u ON g.id_unidad = u.id
            JOIN curso c ON u.id_curso = c.id
            LEFT JOIN planificacion p ON p.id_guion_clase = g.id
            LEFT JOIN usuario_curso uc ON uc.id_curso = c.id
            LEFT JOIN usuario usr ON uc.id_usuario = usr.id
            WHERE g.id = %s
            AND usr.tipo = 2
            LIMIT 1
        """, (guion_id,))
        result = cursor.fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Guion no encontrado")
            
        # Guardar result para usarlo después
        result_data = {
            'titulo': result['titulo'],
            'recursos_aprendizaje': result['recursos_aprendizaje'],
            'contenidos': result['contenidos'],
            'vector_id': result['vector_id'],
            'assistant_id': result['assistant_id'],
            'unidad_nombre': result['unidad_nombre'],
            'profesor': result['profesor'],
            'nombre_curso': result['nombre_curso'],
            'identificacion_clase': result['identificacion_clase']
        }
            
    except Exception as db_error:
        print(f"❌ Error en base de datos: {db_error}")
        raise HTTPException(status_code=500, detail=f"Error accediendo a datos: {str(db_error)}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    try:
        # ✅ Verificar recursos
        recursos_ok, mensaje_recursos = await verificar_recursos_antes_de_procesar(
            result_data['vector_id'], 
            result_data['assistant_id']
        )
        if not recursos_ok:
            raise HTTPException(status_code=400, detail=mensaje_recursos)

        # ✅ Throttling global
        await throttling_global()
        
        # ✅ Crear thread
        nuevo_thread = client.beta.threads.create()
        thread_id = nuevo_thread.id
        print(f"🆕 Thread creado: {thread_id}")

        # ✅ Llamar al endpoint de infografía
        url = f"{url_api}/generar_infografia/{result_data['assistant_id']}"
        data = {
            "thread_id": thread_id,
            "vector_id": result_data['vector_id'],
            "recursos_aprendizaje": result_data['recursos_aprendizaje'],
            "contenidos": result_data['contenidos'],
            "titulo": result_data['titulo']
        }
        
        # ✅ Timeout más largo para infografías (350 segundos)
        data_response = await llamar_api_con_reintentos_y_cancelacion_mejorado(
            url, data, max_intentos=2, tipo_contenido="infografia", timeout_total=350
        )
        
        if not data_response or not data_response.get("imagen_base64"):
            error_msg = data_response.get("error") if data_response else "Error desconocido"
            raise HTTPException(
                status_code=500, 
                detail=f"No se pudo generar la infografía: {error_msg}"
            )
        
        # Ahora data_response tiene ambos: imagen_base64 e imagen_url
        imagen_base64 = data_response["imagen_base64"]
        imagen_url = data_response.get("imagen_url", "")
        
        print(f"✅ Infografía generada - Base64: {len(imagen_base64)} chars, URL: {imagen_url[:50] if imagen_url else 'N/A'}...")
        
        # Extraer nombre_asignatura
        nombre_asignatura = "Asignatura no especificada"
        if result_data.get('identificacion_clase'):
            try:
                identificacion_data = json.loads(result_data['identificacion_clase'])
                nombre_asignatura = identificacion_data.get('nombre_asignatura', result_data.get('nombre_curso', 'Asignatura no especificada'))
            except:
                nombre_asignatura = result_data.get('nombre_curso', 'Asignatura no especificada')
        else:
            nombre_asignatura = result_data.get('nombre_curso', 'Asignatura no especificada')
        
        # GUARDAR EN LA BASE DE DATOS
        conn = connect_db()
        cursor = conn.cursor()
        
        # Obtener número de versión
        cursor.execute("""
            SELECT COALESCE(MAX(version), 0) + 1 as nueva_version
            FROM infografia 
            WHERE id_guion_clase = %s
        """, (guion_id,))
        
        version_result = cursor.fetchone()
        nueva_version = version_result[0] if version_result else 1
        
        # Preparar metadata para guardar
        metadata = {
            "unidad_nombre": result_data.get("unidad_nombre", "Sin nombre"),
            "profesor": result_data.get("profesor", "Docente no especificado"),
            "nombre_curso": result_data.get("nombre_curso", "Curso no especificado"),
            "nombre_asignatura": nombre_asignatura,
            "nombre_unidad": result_data.get("unidad_nombre", "Unidad no especificada"),
            "vector_id": result_data.get("vector_id"),
            "assistant_id": result_data.get("assistant_id"),
            "titulo_guion": result_data.get("titulo", ""),
            "tamano_imagen": len(imagen_base64) if imagen_base64 else 0
        }
        
        # Guardar ambos, pero el base64 solo una parte (opcional)
        # Para MySQL, es mejor no guardar todo el base64
        base64_a_guardar = ""
        if len(imagen_base64) < 10000:  # Solo si es pequeña
            base64_a_guardar = imagen_base64
        else:
            # Guardar solo referencia
            base64_a_guardar = f"[Imagen base64 truncada - tamaño original: {len(imagen_base64)} chars]"
        
        # Insertar nueva infografía
        # BORRAR INGRAFÍAS ANTERIORES DEL MISMO GUION
        cursor.execute("""
            DELETE FROM infografia 
            WHERE id_guion_clase = %s
        """, (guion_id,))

        # Insertar nueva infografía (SIEMPRE con version = 1)
        cursor.execute("""
            INSERT INTO infografia (
                id_guion_clase, 
                titulo, 
                imagen_url,
                imagen_base64,
                prompt_utilizado,
                metadata,
                version
            ) VALUES (%s, %s, %s, %s, %s, %s, 1)
        """, (
            guion_id,
            result_data.get("titulo", ""),
            imagen_url,
            base64_a_guardar,
            "Prompt generado automáticamente",
            json.dumps(metadata)
        ))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print(f"✅ Infografía guardada en BD (v{nueva_version})")
        
        # RETORNAR EXACTAMENTE IGUAL (con la imagen en base64 completa)
        return JSONResponse({
            "infografia": imagen_base64,
            "titulo": result_data['titulo'],
            "guion_id": guion_id,
            "unidad_nombre": result_data.get("unidad_nombre", "Sin nombre"),
            "profesor": result_data.get("profesor", "Docente no especificado"),
            "nombre_curso": result_data.get("nombre_curso", "Curso no especificado"),
            "nombre_asignatura": nombre_asignatura,
            "nombre_unidad": result_data.get("unidad_nombre", "Unidad no especificada")
        })

    except asyncio.TimeoutError:
        print("⏰ Timeout en generación de infografía")
        raise HTTPException(status_code=504, detail="La generación de la infografía tardó demasiado. Intenta nuevamente.")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error en generación de infografía: {e}")
        raise HTTPException(status_code=500, detail=f"Error generando infografía: {str(e)}")
        
    finally:
        # ✅ Limpiar thread
        if thread_id:
            try:
                client.beta.threads.delete(thread_id)
                print(f"🧹 Thread {thread_id} eliminado")
            except Exception as e:
                print(f"⚠️ No se pudo eliminar thread: {e}")


@app.post("/planificacion/{guion_id}/infografia/regenerar")
async def regenerar_infografia(guion_id: int):
    """Endpoint específico para regenerar la infografía"""
    return await generar_infografia(guion_id, "regenerar")


@app.get("/planificacion/{guion_id}/infografia/existe")
async def verificar_infografia_existe(guion_id: int):
    """Verifica si ya existe una infografía para este guión"""
    try:
        conn = connect_db()
        cursor = conn.cursor()

        
        cursor.execute("""
            SELECT id, version, creado_en, titulo, imagen_url 
            FROM infografia 
            WHERE id_guion_clase = %s 
            ORDER BY version DESC 
            LIMIT 1
        """, (guion_id,))
        
        infografia = cursor.fetchone()
        
        return {
            "existe": infografia is not None,
            "version": infografia['version'] if infografia else 0,
            "titulo": infografia['titulo'] if infografia else "",
            "tiene_url": bool(infografia['imagen_url']) if infografia else False,
            "creado_en": infografia['creado_en'].isoformat() if infografia and infografia['creado_en'] else None
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# Función auxiliar para descargar imágenes
async def _download_image(image_url: str) -> Optional[str]:
    """Descarga imagen desde URL y convierte a base64"""
    try:
        print(f"📥 Descargando imagen: {image_url}")
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(image_url)
            
            if response.status_code == 200:
                # Verificar que sea imagen
                content_type = response.headers.get('content-type', '')
                if 'image' not in content_type:
                    print(f"⚠️ No es imagen: {content_type}")
                    return None
                
                # Convertir a base64
                import base64
                image_base64 = base64.b64encode(response.content).decode('utf-8')
                
                print(f"✅ Imagen descargada: {len(image_base64)} chars base64")
                return image_base64
            else:
                print(f"❌ HTTP {response.status_code} descargando imagen")
                return None
                
    except httpx.TimeoutException:
        print("⏰ Timeout descargando imagen")
        return None
    except Exception as e:
        print(f"❌ Error descargando: {e}")
        return None

async def llamar_api_con_reintentos_y_cancelacion_mejorado(
    url, data, max_intentos=3, tipo_contenido="infografia", timeout_total=350
):
    """Versión mejorada con cancelación de procesos pendientes"""
    
    for intento in range(1, max_intentos + 1):
        print(f"🔄 Intento {intento} de {max_intentos} para {tipo_contenido}")
        
        try:
            # ✅ Configurar timeout específico por tipo de contenido
            if tipo_contenido == "infografia":
                timeout = timeout_total  # 350 segundos para infografías
            else:
                timeout = 120.0
            
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, data=data)
                
            if response.status_code == 200:
                data_response = response.json()
                
                # ✅ VERIFICACIÓN MEJORADA
                if data_response.get("imagen_base64"):
                    print(f"✅ {tipo_contenido} generado exitosamente (Intento {intento})")
                    return data_response
                elif data_response.get("infografia"):  # ✅ Manejo temporal para compatibilidad
                    print("⚠️ Usando campo 'infografia' (debería ser 'imagen_base64')")
                    data_response["imagen_base64"] = data_response["infografia"]
                    return data_response
                elif data_response.get("error"):
                    print(f"❌ Error en API: {data_response['error']}")
                    if intento == max_intentos:
                        return {"error": data_response['error']}
                else:
                    print(f"🚫 Respuesta sin contenido válido: {data_response.keys()}")
                    if intento == max_intentos:
                        return {"error": "Respuesta sin contenido válido"}
            else:
                print(f"❌ Error HTTP {response.status_code}: {response.text}")
                if intento == max_intentos:
                    return {"error": f"Error HTTP {response.status_code}"}
                    
        except httpx.TimeoutException:
            print(f"⏰ Timeout en intento {intento} para {tipo_contenido}")
            
            # ✅ NOTA: El proceso en la API podría seguir corriendo
            # Pero al menos no hacemos otro intento inmediato que duplique procesos
            if intento == max_intentos:
                return {"error": f"Timeout después de {timeout_total} segundos"}
            
            # ✅ Esperar más tiempo antes de reintentar para dar chance al proceso anterior
            wait_time = 10 * intento  # 10, 20, 30 segundos
            print(f"⏳ Esperando {wait_time}s antes de reintentar...")
            await asyncio.sleep(wait_time)
            
        except Exception as e:
            print(f"❌ Error en intento {intento}: {str(e)}")
            if intento == max_intentos:
                return {"error": str(e)}
        
        if intento < max_intentos:
            wait_time = 5 * intento  # Backoff exponencial
            print(f"⏳ Esperando {wait_time}s antes del próximo intento...")
            await asyncio.sleep(wait_time)
    
    return {"error": "Todos los intentos fallaron"}

#################################################################
#################################################################
#################################################################




##########################################################################
##################
################## Funcion para compartir material
##################
#########################################################################


@app.post("/enviar-correo")
async def enviar_correo_con_archivos(
    destinatarios: str = Form(...),
    asunto: str = Form(...),
    mensaje: str = Form(...),
    remitente: str = Form(...),
    archivos: list[UploadFile] = File(...)
):
    try:
        print("📧 Iniciando envío de correo...")
        
        # Parsear destinatarios
        lista_destinatarios = json.loads(destinatarios)
        print(f"👥 Destinatarios: {lista_destinatarios}")
        print(f"📝 Asunto: {asunto}")
        print(f"📎 Archivos recibidos: {len(archivos)}")

        # ✅ CREAR ZIP EN MEMORIA
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for archivo in archivos:
                print(f"📦 Agregando al ZIP: {archivo.filename}")
                
                # Leer contenido del archivo
                contenido = await archivo.read()
                
                # Usar nombre seguro para el ZIP (sin espacios raros)
                nombre_seguro = archivo.filename.replace(' ', '_').replace('/', '_')
                
                # Agregar archivo al ZIP
                zipf.writestr(nombre_seguro, contenido)

        # Obtener el contenido del ZIP
        zip_buffer.seek(0)
        zip_content = zip_buffer.getvalue()
        zip_buffer.close()

        print(f"📊 Tamaño del ZIP: {len(zip_content) / (1024*1024):.2f} MB")

        # Crear mensaje
        msg = MIMEMultipart()
        msg['From'] = remitente
        msg['To'] = ", ".join(lista_destinatarios)
        msg['Subject'] = asunto

        # Agregar cuerpo del mensaje
        msg.attach(MIMEText(mensaje, 'plain'))

        # ✅ AGREGAR SOLO EL ZIP COMO ADJUNTO (en lugar de todos los archivos)
        part = MIMEBase('application', 'zip')
        part.set_payload(zip_content)
        encoders.encode_base64(part)
        part.add_header(
            'Content-Disposition',
            f'attachment; filename="Materiales_Estudio.zip"'
        )
        msg.attach(part)

        # Enviar correo
        print("🚀 Enviando correo con ZIP...")
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_INSTITUCIONAL, EMAIL_PASSWORDI)
        
        text = msg.as_string()
        server.sendmail(remitente, lista_destinatarios, text)
        server.quit()

        print("✅ Correo con ZIP enviado exitosamente")
        return {
            "status": "success",
            "message": f"Correo enviado a {len(lista_destinatarios)} destinatarios con ZIP que contiene {len(archivos)} archivos",
            "destinatarios": len(lista_destinatarios),
            "archivos_en_zip": len(archivos),
            "tamaño_zip_mb": f"{len(zip_content) / (1024*1024):.2f}"
        }

    except Exception as e:
        print(f"❌ Error enviando correo: {e}")
        raise HTTPException(status_code=500, detail=f"Error al enviar correo: {str(e)}")
#################################################################
#################################################################
#################################################################

##########################################################################
##################
################## Funcion para limpiar gpt
##################
#########################################################################
def get_preserved_assistant_ids():
    """Obtiene todos los assistant_ids que deben preservarse de la base de datos"""
    preserved_ids = set()
    
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        
        # Obtener assistant_ids de la tabla guion_clase
        cursor.execute("SELECT assistant_id FROM guion_clase WHERE assistant_id IS NOT NULL AND assistant_id != ''")
        for row in cursor.fetchall():
            preserved_ids.add(row[0])
        
        # Obtener assistant_ids de la tabla unidad
        cursor.execute("SELECT assistant_id FROM unidad WHERE assistant_id IS NOT NULL AND assistant_id != ''")
        for row in cursor.fetchall():
            preserved_ids.add(row[0])
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"Error al obtener assistant_ids de la BD: {e}")
    
    return preserved_ids

def get_preserved_vector_store_ids():
    """Obtiene todos los vector_ids que deben preservarse de la base de datos"""
    preserved_ids = set()
    
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        
        # Obtener vector_ids de la tabla guion_clase
        cursor.execute("SELECT vector_id FROM guion_clase WHERE vector_id IS NOT NULL AND vector_id != ''")
        for row in cursor.fetchall():
            preserved_ids.add(row[0])
        
        # Obtener vector_ids de la tabla unidad
        cursor.execute("SELECT vector_id FROM unidad WHERE vector_id IS NOT NULL AND vector_id != ''")
        for row in cursor.fetchall():
            preserved_ids.add(row[0])
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"Error al obtener vector_ids de la BD: {e}")
    
    return preserved_ids

def get_preserved_file_ids():
    """Obtiene todos los file_ids que deben preservarse de la base de datos"""
    preserved_ids = set()
    
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        
        # Obtener file_ids de la tabla guion_clase
        cursor.execute("SELECT file_id FROM guion_clase WHERE file_id IS NOT NULL AND file_id != ''")
        for row in cursor.fetchall():
            preserved_ids.add(row[0])
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"Error al obtener file_ids de la BD: {e}")
    
    return preserved_ids

def delete_all_assistants():
    """Elimina solo los asistentes que NO están en la base de datos"""
    preserved_assistant_ids = get_preserved_assistant_ids()
    
    print(f"Preservando {len(preserved_assistant_ids)} asistentes de la BD")
    
    try:
        assistants = client.beta.assistants.list()
        # Convertir a lista para evitar problemas de paginación durante la eliminación
        assistants_list = list(assistants)
        
        for assistant in assistants_list:
            if assistant.id in preserved_assistant_ids:
                print(f"✅ Preservando assistant {assistant.id} (está en BD)")
                continue
            
            try:
                client.beta.assistants.delete(assistant.id)
                print(f"🗑️ Assistant {assistant.id} eliminado exitosamente.")
            except Exception as e:
                print(f"❌ Error eliminando assistant {assistant.id}: {e}")
    except Exception as e:
        print(f"❌ Error obteniendo lista de asistentes: {e}")

def delete_all_vector_stores():
    """Elimina solo los vector stores que NO están en la base de datos"""
    preserved_vector_ids = get_preserved_vector_store_ids()
    
    print(f"Preservando {len(preserved_vector_ids)} vector stores de la BD")
    
    try:
        vector_stores = client.beta.vector_stores.list()
        # Convertir a lista para evitar problemas de paginación durante la eliminación
        vector_stores_list = list(vector_stores)
        
        for vector_store in vector_stores_list:
            if vector_store.id in preserved_vector_ids:
                print(f"✅ Preservando vector store {vector_store.id} (está en BD)")
                continue
            
            try:
                client.beta.vector_stores.delete(vector_store.id)
                print(f"🗑️ Vector store {vector_store.id} eliminado exitosamente.")
            except Exception as e:
                # Si el vector store ya no existe, solo lo registramos y continuamos
                if "not found" in str(e).lower():
                    print(f"⚠️ Vector store {vector_store.id} ya fue eliminado")
                else:
                    print(f"❌ Error eliminando vector store {vector_store.id}: {e}")
    except Exception as e:
        print(f"❌ Error obteniendo lista de vector stores: {e}")

def delete_all_files():
    """Elimina solo los archivos que NO están en la base de datos"""
    preserved_file_ids = get_preserved_file_ids()
    
    print(f"Preservando {len(preserved_file_ids)} archivos de la BD")
    
    try:
        files = client.files.list()
        # Convertir a lista para evitar problemas de paginación durante la eliminación
        files_list = list(files)
        
        for file in files_list:
            if file.id in preserved_file_ids:
                print(f"✅ Preservando file {file.id} (está en BD)")
                continue
            
            try:
                client.files.delete(file.id)
                print(f"🗑️ File {file.id} eliminado exitosamente.")
            except Exception as e:
                # Si el archivo ya no existe, solo lo registramos y continuamos
                if "not found" in str(e).lower():
                    print(f"⚠️ File {file.id} ya fue eliminado")
                else:
                    print(f"❌ Error eliminando file {file.id}: {e}")
    except Exception as e:
        print(f"❌ Error obteniendo lista de archivos: {e}")

# Versión alternativa más robusta con manejo de errores mejorado
def delete_all_vector_stores_safe():
    """Versión más segura para eliminar vector stores"""
    preserved_vector_ids = get_preserved_vector_store_ids()
    
    print(f"Preservando {len(preserved_vector_ids)} vector stores de la BD")
    
    try:
        # Obtener todos los vector stores de forma segura
        vector_stores = list(client.beta.vector_stores.list())
        print(f"Encontrados {len(vector_stores)} vector stores en total")
        
        deleted_count = 0
        error_count = 0
        
        for vector_store in vector_stores:
            if vector_store.id in preserved_vector_ids:
                print(f"✅ Preservando vector store {vector_store.id} (está en BD)")
                continue
            
            try:
                # Verificar si existe antes de intentar eliminar
                client.beta.vector_stores.retrieve(vector_store.id)
                # Si llegamos aquí, existe - procedemos a eliminar
                client.beta.vector_stores.delete(vector_store.id)
                print(f"🗑️ Vector store {vector_store.id} eliminado exitosamente.")
                deleted_count += 1
            except Exception as e:
                if "not found" in str(e).lower():
                    print(f"⚠️ Vector store {vector_store.id} ya fue eliminado")
                else:
                    print(f"❌ Error eliminando vector store {vector_store.id}: {e}")
                    error_count += 1
        
        print(f"Resumen: {deleted_count} eliminados, {error_count} errores")
        
    except Exception as e:
        print(f"❌ Error crítico en delete_all_vector_stores_safe: {e}")


def NUKE():
    delete_all_assistants()
    delete_all_vector_stores()
    delete_all_files()

#NUKE()

#################################################################
#################################################################
#################################################################


# En tu backend (app.py o similar)
@app.post("/enviar-correo-con-link")
async def enviar_correo_con_link(request: Request):
    """Envía un correo con un link de Google Drive (sin adjuntos)"""
    try:
        data = await request.json()
        
        destinatarios = data.get('destinatarios', [])
        asunto = data.get('asunto', 'Material de estudio')
        mensaje = data.get('mensaje', '')
        drive_link = data.get('drive_link', '')
        remitente = data.get('remitente', 'iwanttoteach123@gmail.com')
        
        if not destinatarios:
            return {"success": False, "error": "No hay destinatarios"}
        
        if not drive_link:
            return {"success": False, "error": "No hay link de Google Drive"}
        
        # Construir el mensaje HTML con el link
        mensaje_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <h2 style="color: #333;">{asunto}</h2>
                <div style="white-space: pre-line; margin: 20px 0; padding: 15px; background: #f8f9fa; border-radius: 5px;">
                    {mensaje.replace('\n', '<br>')}
                </div>
                
                <div style="margin: 30px 0; padding: 20px; background: #e8f5e8; border-radius: 8px; text-align: center;">
                    <h3 style="color: #2e7d32;">📚 Material de Estudio</h3>
                    <p>Haz clic en el siguiente enlace para acceder al material:</p>
                    <a href="{drive_link}" 
                       style="display: inline-block; padding: 12px 24px; 
                              background: #4285f4; color: white; 
                              text-decoration: none; border-radius: 5px;
                              font-weight: bold; margin: 10px 0;">
                        📥 Descargar Material desde Google Drive
                    </a>
                    <p style="margin-top: 10px; font-size: 14px; color: #666;">
                        O copia este enlace: <br>
                        <code style="background: #f1f3f4; padding: 5px; border-radius: 3px;">
                            {drive_link}
                        </code>
                    </p>
                </div>
                
                <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd; font-size: 12px; color: #666;">
                    <p>Este correo fue enviado por {remitente}</p>
                    <p>El material está disponible en Google Drive. Solo quienes tengan este enlace pueden acceder.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Configurar el correo
        msg = MIMEMultipart('alternative')
        msg['Subject'] = asunto
        msg['From'] = f"AI-Want-2-Teach <{remitente}>"
        msg['To'] = ', '.join(destinatarios)
        
        # Versión texto plano
        mensaje_texto = f"{mensaje}\n\n🔗 Enlace para descargar: {drive_link}"
        
        # Adjuntar ambas versiones
        part1 = MIMEText(mensaje_texto, 'plain')
        part2 = MIMEText(mensaje_html, 'html')
        
        msg.attach(part1)
        msg.attach(part2)
        
        # Enviar correo (usa tu función existente de envío de correos)
        # Asumiendo que ya tienes una función send_email
        await send_email(destinatarios, msg)
        
        return {
            "success": True,
            "message": f"Correo enviado exitosamente a {len(destinatarios)} destinatario(s)",
            "destinatarios": destinatarios,
            "drive_link": drive_link
        }
        
    except Exception as e:
        logger.error(f"Error enviando correo con link: {e}")
        return {"success": False, "error": str(e)}
    
    # Si ya tienes send_email, puedes reutilizarla:
async def send_email(destinatarios, msg):
    """Envía un correo usando tu configuración SMTP existente"""
    try:
        # Tu código SMTP existente
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login('iwanttoteach123@gmail.com', EMAIL_PASSWORDI)  # Usa variables de entorno
            server.send_message(msg)
        
        logger.info(f"Correo enviado a {len(destinatarios)} destinatarios")
        return True
        
    except Exception as e:
        logger.error(f"Error enviando correo: {e}")
        raise




@app.get("/planificacion/{guion_id}/evaluacion_formativa")
async def generar_evaluacion_formativa(
    guion_id: int,
    momento: str = Query(...),
    tipo: str = Query(...)
):
    print("📥 Generando evaluación formativa", guion_id, momento, tipo)

    conn = connect_db()
    cursor = conn.cursor()


    try:
        # 1️⃣ Obtener datos del guion + planificacion
        cursor.execute("""
            SELECT 
                g.vector_id,
                g.assistant_id,
                p.evaluaciones_formativas,
                p.estrategias_didacticas
            FROM guion_clase g
            JOIN planificacion p ON p.id_guion_clase = g.id
            WHERE g.id = %s
            LIMIT 1
        """, (guion_id,))

        data = cursor.fetchone()
        if not data:
            raise HTTPException(status_code=404, detail="Guion no encontrado")

        evaluaciones = json.loads(data["evaluaciones_formativas"] or "[]")
        estrategias = json.loads(data["estrategias_didacticas"] or "[]")

        # 2️⃣ Filtrar evaluación por momento + tipo
        evaluacion = next(
            (e for e in evaluaciones if e["momento"] == momento and e["tipo"] == tipo),
            None
        )

        if not evaluacion:
            raise HTTPException(status_code=404, detail="Evaluación no encontrada")

        estrategia = next(
            (e for e in estrategias if e["momento"] == momento),
            None
        )

        # 3️⃣ Crear thread
        nuevo_thread = client.beta.threads.create()
        thread_id = nuevo_thread.id

    finally:
        cursor.close()
        conn.close()

    # 4️⃣ Llamar a la API IA
    url = f"{url_api}/generar_evaluacion_formativa/{data['assistant_id']}"
    payload = {
        "thread_id": thread_id,
        "vector_id": data["vector_id"],
        "evaluacion": evaluacion,
        "estrategia": estrategia
    }

    respuesta = await llamar_api_con_reintentos_y_cancelacion(
        url,
        payload,
        tipo_contenido="evaluacion_formativa"
    )

    return {
        "evaluacion_formativa": respuesta.get("evaluacion_formativa"),
        "momento": momento,
        "tipo": tipo
    }

    