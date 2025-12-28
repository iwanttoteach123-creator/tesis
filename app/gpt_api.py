import os
from pathlib import Path
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"   # -> backend/.env
load_dotenv(ENV_PATH)

from openai import OpenAI

import asyncio
import time
from fastapi import HTTPException, FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
prompt1 = """Analiza el proyecto HTML que te voy a compartir en formato .tar y escribe un breve feedback sobre él, indicando los errores y fortalezas. Soy un estudiante que está cursando un curso
de programación y quiero asegurarme de que mi proyecto esté correcto y cumpla con la
actividad asignada. La actividad asignada es la siguiente: """
prompt2 = """El feedback debe estar estructurado en una lista anidada con tres encabezados principales: "Errores", "Mejoras sugeridas" y "Fortalezas", cada uno con 1 a 3
ítems con una breve descripción (ejemplo: "Uso de caracteres especiales: Asegúrate de
usar entidades HTML como &ntilde; para la letra ñ para garantizar compatibilidad.").
Además, menciona explícitamente si faltó cumplir alguna instrucción de la actividad
asignada. Por ejemplo: "Te faltó realizar la pregunta 3 de la actividad." Considera los
contenidos que se me han enseñado en el curso para generar el feedback. Utiliza un tono
formal y no incluyas introducciones ni comentarios adicionales, solo proporciona la lista
anidada con el feedback."""
instructions = '''Eres un experimentado profesor de programación especializado en revisión de código. 
Tu objetivo es evaluar el código entregado por tus alumnos, identificando errores y 
destacando sus fortalezas. Debes interpretar el código proporcionado y proporcionar 
feedback detallado y preciso. En tu retroalimentación, señala los errores más críticos, 
ofrece sugerencias claras y prácticas para mejorar la solución, y destaca las partes del 
código que estén bien implementadas. Usa un tono constructivo y pedagógico para fomentar 
el aprendizaje y el desarrollo de habilidades.
Aparte si te lo piden debes generar preguntas para evaluar a tus alumnos basado en los archivos que contengas. Estas preguntas pueden ser de desarrollo, alternativas, o verdadero y falso dependiendo de lo solicitado. 
'''

'''
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)'


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'pdf'}

async def create_assistant(descripcion, instruccions, model="gpt-4-turbo-preview"):
    try:
        assistant = openai.Assistant.create(
            description=descripcion,
            instructions=instruccions,
            model=model,
            tools=[{"type": "file_search"}]
        )
        return assistant
    except openai.Error as e:
        raise HTTPException(status_code=500, detail=str(e))

async def upload_file_to_assistant(file, assistant_id):
    if not allowed_file(file.filename):
        raise HTTPException(status_code=400, detail="File type not allowed")

    file_location = os.path.join(UPLOAD_FOLDER, file.filename)
    with open(file_location, "wb") as f:
        f.write(file.file.read())

    try:
        response = openai.File.create(file=open(file_location), purpose="fine-tune")
        file_id = response['id']
        
        # Optionally link the file with the assistant
        # Here you would add logic to associate the file_id with the assistant_id if needed
        
        return file_id
    except openai.Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    
'''    
openai_key = os.getenv("OPENAI_API_KEY")
if not openai_key:
    raise RuntimeError("❌ OPENAI_API_KEY no está definida en backend/.env")

# Configurar el cliente OpenAI con tu clave API para iniciar sesion
client = OpenAI(api_key=openai_key)

#NOGPT
async def crear_prompt(texto):
    # Crear el nuevo prompt con el texto recibido entre prompt1 y prompt2
    nuevo_prompt = f"{prompt1}\n{texto}\n{prompt2}"
    return nuevo_prompt

def crear_assistant():
    '''
    Función para crear un asistente con vector store
    Retorna: assistant_id, vector_store_id
    '''
    try:
        # Primero crear el vector store
        vector_store = client.beta.vector_stores.create(
            name=f"VectorStore_{int(time.time())}"
        )
        print(f"🗂️ Vector Store creado: {vector_store.id}")

        # Luego crear el assistant con el vector store
        assistant = client.beta.assistants.create(
            instructions=instructions,
            tools=[{"type": "file_search"}],
            tool_resources={
                "file_search": {
                    "vector_store_ids": [vector_store.id]
                }
            },
            model="gpt-4o-mini",
        )
        print(f"👨‍💼 Assistant creado: {assistant.id}")

        return assistant.id, vector_store.id

    except Exception as e:
        print(f"❌ Error creando assistant: {str(e)}")
        raise


async def verificar_estado_vector_store(vector_store_id: str):
    """
    Verifica el estado actual del vector store
    """
    try:
        # Verificar vector store
        vs = client.beta.vector_stores.retrieve(vector_store_id)
        print(f"🔍 Vector Store: {vs.id}")
        print(f"   - Estatus: {vs.status}")
        print(f"   - Uso: {vs.usage_bytes} bytes")
        print(f"   - Archivos: {vs.file_counts}")

        # Listar archivos en el vector store
        files = client.beta.vector_stores.files.list(vector_store_id=vector_store_id)
        
        print(f"   - Archivos presentes: {len(files.data)}")
        for file in files.data:
            print(f"     📄 {file.id}: {file.status} (Tipo: {file.object})")

        return vs

    except Exception as e:
        print(f"❌ Error verificando vector store: {e}")
        return None
async def limpiar_vector_store(vector_store_id: str):
    """Elimina todos los archivos de un vector store y espera a que se complete"""
    try:
        files = client.beta.vector_stores.files.list(vector_store_id=vector_store_id)
        print(f"🗑️ Eliminando {len(files.data)} archivos...")
        
        # Eliminar todos los archivos
        for file in files.data:
            client.beta.vector_stores.files.delete(
                vector_store_id=vector_store_id,
                file_id=file.id
            )
            print(f"   - Solicitada eliminación de: {file.id}")
        
        # ✅ ESPERAR A QUE TODAS LAS ELIMINACIONES SE COMPLETEN
        print("⏳ Esperando a que se complete la limpieza...")
        await asyncio.sleep(5)  # Espera inicial
        
        # Verificar periódicamente hasta que esté vacío
        max_attempts = 10
        for attempt in range(max_attempts):
            remaining_files = client.beta.vector_stores.files.list(vector_store_id=vector_store_id)
            
            if len(remaining_files.data) == 0:
                print("✅ Vector store completamente limpiado")
                return
                
            print(f"🔄 Intentando {attempt + 1}/{max_attempts}: {len(remaining_files.data)} archivos pendientes...")
            await asyncio.sleep(3)  # Esperar entre intentos
            
        print("⚠️  Algunos archivos aún pueden estar en proceso de eliminación")
            
    except Exception as e:
        print(f"❌ Error limpiando: {e}")





#Funcion para actualizar vector_store recibe id vector store y archifo (file), retorna el ID DEL ARCHIVO
async def actualizar_vector_store(vector_store_id, archivo):
    '''
    Actualiza el vector store con un nuevo archivo usando batches.
    Retorna: file_id, batch_id
    '''
    # Subir archivo a OpenAI
    nuevo_archivo = client.files.create(
        file=archivo,
        purpose='assistants'
    )

    # Crear batch con el archivo subido
    batch_add = client.beta.vector_stores.file_batches.upload_and_poll(
        vector_store_id=vector_store_id,
        files=[nuevo_archivo.id]
    )

    print(f"📦 Batch creado: {batch_add.id} para vector {vector_store_id}")
    return nuevo_archivo.id, batch_add.id


async def subir_corpus(assistant_id: str, upload_file: UploadFile, vector_store_id: str):
    '''
    Versión simple - sube el archivo y retorna sin esperar verificación
    '''
    try:
        print(f"🔍 Procesando: {upload_file.filename}")
        
        # 1️⃣ Leer y subir archivo a OpenAI
        file_content = await upload_file.read()
        from io import BytesIO
        file_like_object = BytesIO(file_content)
        
        uploaded_file = client.files.create(
            file=(upload_file.filename, file_like_object, upload_file.content_type),
            purpose="assistants"
        )
        
        print(f"📄 Archivo subido a OpenAI: {uploaded_file.id}")

        # 2️⃣ Agregar archivo al vector store
        vector_store_file = client.beta.vector_stores.files.create(
            vector_store_id=vector_store_id,
            file_id=uploaded_file.id
        )
        
        print(f"📦 Archivo agregado al vector store: {vector_store_file.id}")

        # 3️⃣ ✅ SOLUCIÓN: Esperar un tiempo fijo sin verificar estado
        print("⏳ Esperando procesamiento del archivo...")
        await asyncio.sleep(10)  # Espera fija de 10 segundos

        # Verificación rápida final (opcional)
        try:
            file_status = client.beta.vector_stores.files.retrieve(
                vector_store_id=vector_store_id,
                file_id=vector_store_file.id
            )
            print(f"📊 Estado final: {file_status.status}")
        except Exception as e:
            print(f"⚠️  No se pudo verificar estado final: {e}")

        print("✅ Archivo enviado para procesamiento")
        return {
            "file_id": uploaded_file.id, 
            "batch_id": vector_store_file.id
        }

    except Exception as e:
        print(f"❌ Error en subir_corpus_simple: {str(e)}")
        raise Exception(f"Error subiendo archivo: {str(e)}")

async def eliminar_archivo(archivo_id):
    """
    Elimina un archivo de la API de GPT de OpenAI utilizando su ID de manera asíncrona.
    
    Args:
        archivo_id (str): El ID del archivo a eliminar.
        
    Returns:
        dict: La respuesta de la API de OpenAI.
    """
    try:
        response = await asyncio.to_thread(client.files.delete, archivo_id)
        print(f"Archivo {archivo_id} eliminado correctamente.")
        return response

    except Exception as e:
        print(f"Error al eliminar el archivo: {e}")

def mostrar_mensajes_assistant(messages):
    ''''
    fUNCION AUXILIAR Para obtener el resultado del prompt como texto plano
    '''

    mensajes_assistant = [message for message in messages if message.role == "assistant"]
    mensajes_texto = []

    for message in mensajes_assistant:
        if isinstance(message.content, list):
            for content in message.content:
                print(f"Content type: {type(content)}")
                # Acceso basado en la estructura esperada del objeto
                if content.type == 'text':
                    print(content.text.value)
                    mensajes_texto.append(content.text.value.replace('\n', '<br>'))

                    if hasattr(content.text, 'annotations'):
                        for annotation in content.text.annotations:
                            print(f"Annotation Text: {annotation.text}")
                            if hasattr(annotation, 'file_path') and hasattr(annotation.file_path, 'file_id'):
                                print(f"File_Id: {annotation.file_path.file_id}")
                                annotation_data = client.files.content(annotation.file_path.file_id)
                                annotation_data_bytes = annotation_data.read()

                                filename = annotation.text.split('/')[-1]

                                with open(f"{filename}", "wb") as file:
                                    file.write(annotation_data_bytes)
                            else:
                                print("La anotación no tiene un archivo asociado.")
                elif content.type == 'image_file':
                    print(f"Image File ID: {content.image_file.file_id}")
        else:
            print("El contenido del mensaje no está en el formato esperado.")

    return mensajes_texto

#NO GPT
def obtener_prompt(messages):
    for thread_message in messages.data:
        # Iterate over the 'content' attribute of the ThreadMessage, which is a list
        for content_item in thread_message.content:
            # Assuming content_item is a MessageContentText object with a 'text' attribute
            # and that 'text' has a 'value' attribute, print it
            return(content_item.text.value)

async def obtener_feedback(assistant_id, archivo, prompt):
    archivo = client.files.create(file=archivo, purpose='assistants')
    thread = client.beta.threads.create(
        messages=[
            {
                "role": "user",
                "content": prompt,
                "attachments": [
                    {
                        "file_id": archivo.id,
                        "tools": [{"type": "code_interpreter"}]
                    }
                ]
            }
        ]
    )
    run = client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=assistant_id
    )
    
    while run.status not in ["completed", "failed"]:
        run = client.beta.threads.runs.retrieve(
            thread_id=thread.id,
            run_id=run.id
        )
        print(run.status)
        await asyncio.sleep(1)  # Use asyncio.sleep to not block the event loop

    messages = client.beta.threads.messages.list(
        thread_id=thread.id,
    )
    id = archivo.id
    #client.files.delete(archivo.id) ahora se borra al borrar la actividad pero se podria eliminar altok
    return mostrar_mensajes_assistant(messages), id

# Para ejecutar la función asincrónica desde un contexto sincrónico
def obtener_feedback_sync(archivo, prompt):
    return asyncio.run(obtener_feedback(archivo, prompt))

#NUKE()


