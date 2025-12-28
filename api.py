from fastapi import APIRouter, File, HTTPException, UploadFile, Form
from openai import OpenAI
import os


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise RuntimeError(
        "Falta OPENAI_API_KEY en el entorno. "
        "Defínela en backend/.env o como variable de entorno del sistema."
    )

client = OpenAI(api_key=OPENAI_API_KEY)
router = APIRouter()



import asyncio
import re
import time
import httpx
import base64
import json
from typing import Dict, Any, Optional, List
from datetime import datetime

# =========================================================
# 1) Cargar variables de entorno (.env)
#    - Busca automáticamente .env en el directorio actual
#      o en el path del proyecto.
# =========================================================


# 2) Leer variables (con defaults razonables)
# =========================================================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
NANO_BANANA_API_KEY = os.getenv("NANO_BANANA_API_KEY")
key = os.getenv("OPENAI_API_KEY")


proposito = os.getenv("PROPOSITO", "assistants")
modelo = os.getenv("MODELO", "gpt-4o")

instrucciones = os.getenv("INSTRUCCIONES", "")
PROMPT_1 = os.getenv("PROMPT_1", "")
PROMPT_2 = os.getenv("PROMPT_2", "")

# =========================================================
# 3) Validaciones mínimas (fail fast con mensaje claro)
# =========================================================
if not OPENAI_API_KEY:
    raise RuntimeError(
        "Falta OPENAI_API_KEY en el entorno. "
        "Defínela en backend/.env o como variable de entorno del sistema."
    )

# Si Nano Banana es opcional, no lo mates (solo avisa)
# if not NANO_BANANA_API_KEY:
#     print("⚠️ NANO_BANANA_API_KEY no está definida (opcional).")

# =========================================================
# 4) Cliente OpenAI (UNA sola vez)
# ==============================================
client = OpenAI(api_key=OPENAI_API_KEY)

# =========================================================
# 5) Router FastAPI
# =========================================================
router = APIRouter()

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


#####################
###   Assistant   ###
#####################

### AGREGAR Asistente
@router.post("/create-assistant/")
async def create_assistant(name: str):  
    assistant = client.beta.assistants.create(
        name=name,
        instructions=instrucciones,
        model=modelo,
        tools=[{"type": "code_interpreter"}, {"type": "file_search"}]
    )
    
    return assistant.id


### ACTUALIZAR Asistente
@router.put("/update-assistant/{assistant_id}/{vector_id}")
async def update_assistant(assistant_id: str, vector_id: str):
    assistant = client.beta.assistants.update(
        assistant_id=assistant_id,
        tool_resources={"file_search": {"vector_store_ids": [vector_id]}},
    )
    return assistant.id
    
### BORRAR Asistente
@router.delete("/delete-assistant/{assistant_id}")
async def delete_assistant( assistant_id: str):
    try:
        ### Llama a la API de OpenAI para eliminar el archivo
        respuesta = client.beta.assistants.delete(assistant_id)
        
    except Exception as e:
        print("Error deleting file:", e)
        raise HTTPException(status_code=500, detail="Failed to delete Assistant")
    
    return {"message": "El Asistente se a eliminado de OpenAI exitosamente.", "response": respuesta}



##################
###   Vector   ###
##################

### AGREGAR Vector
@router.post("/create-vector/{assistant_id}")
async def create_vector(assistant_id: str):
    vector_store = client.beta.vector_stores.create()
    await update_assistant(assistant_id, vector_store.id)

    return vector_store.id


### ACTUALIZAR Vector
@router.put("/update-vector/{assistant_id}/{vector_id}/{file_id}")
async def update_vector(assistant_id: str, vector_id: str, file_id: str):
    batch_add = client.beta.vector_stores.file_batches.create(
        vector_store_id=vector_id,
        file_ids=[file_id]
    )
    i =1
    while i < 3: 
        print(batch_add.status) # Simulate the status update
        await asyncio.sleep(1)
        i+=1
    await update_assistant(assistant_id, vector_id)


    return vector_id

### BORRAR Vector
@router.delete("/delete-vector/{vector_id}/")
async def delete_vector(vector_id: str):
    try:
        respuesta = client.beta.vector_stores.delete(vector_id)

    except Exception as e:
        print("Error deleting file:", e)
        raise HTTPException(status_code=500, detail="Failed to delete Vector")
    
    return {"message": "El Vector se a eliminado de OpenAI exitosamente.", "response": respuesta}




####################
###   Archivos   ###
####################


### AGREGAR archivo
### Aqui se suben los archivos a OpenAI y te devuelve el ID.
### Se necesita el ID del assistants y del vector donde se subira
@router.post("/upload-file/{assistant_id}/{vector_id}")
async def upload_file( assistant_id: str, vector_id: str, archivo: UploadFile = File(...)):
    try:
        ### 
        archivo_contenido = await archivo.read()
        ### Llama a la API de OpenAI para subir el archivo
        respuesta = client.files.create(
            file=(archivo.filename, archivo_contenido),
            purpose=proposito,
        )

        file_id = respuesta.id

    except Exception as e:
        print("Error uploading file to OpenAI:", e)
        return {"error": "Failed to upload file to OpenAI"}
    

    ### Actualiza el vector y el asistente
    try:
        await update_vector(assistant_id, vector_id, file_id)
    except Exception as e:
        print("Error updating:", e)
        raise HTTPException(status_code=500, detail="Failed to update")


    ### Se retorna el ID del archivo
    return {"file_id": file_id}


### ACTUALIZAR archivo
@router.put("/update-file/{assistant_id}/{vector_id}/{file_id}")
async def update_file(assistant_id: str, vector_id: str, file_id: str, archivo: UploadFile = File(...)):

    await delete_file(file_id)
    file_id_nuevo = await upload_file(assistant_id, vector_id, archivo)
    return {"file_id": file_id_nuevo}


### BORRAR archivo
### Aqui se borran los archivos de OpenAI usando la ID.
@router.delete("/delete-file/{file_id}")
async def delete_file( file_id: str):
    try:
        ### Llama a la API de OpenAI para eliminar el archivo
        respuesta = client.files.delete(id=file_id)
        
    
    except Exception as e:
        print("Error deleting file:", e)
        raise HTTPException(status_code=500, detail="Failed to delete file")
    
    
    return {"message": "El archivo se a eliminado de OpenAI exitosamente.", "response": respuesta}
    


####################
###   FeedBack   ###
####################

@router.post('/Feedback/{assistant_id}/')
async def obtener_feedback(
    assistant_id: str,
    requerimientos: str = Form(...),  # Recibe los datos como form data
    desarrollo: str = Form(...)  # Recibe los datos como form data
):
    try:
        print(f"Assistant ID: {assistant_id}")
        print(f"Requerimientos: {requerimientos}")
        print(f"Desarrollo: {desarrollo}")
        prompt = f"""Revisa el código de este ejercicio de acuerdo a los siguientes requerimientos: {requerimientos}.

        Por cada punto de los requerimientos: - Incluye el requerimiento y la descripción. - Proporciona un detalle completo sobre el grado de cumplimiento.
        - Indica la nota obtenida (número entre 0 y el puntaje máximo), ej: "nota_obtenida: 0.5 de 1".
        - La nota debe ser un número decimal.
        - No agregues comentarios adicionales ni uses criterios como "cumple" o "no cumple".
        - Si hay múltiples errores, asigna 0 puntos.
        - No incluyas una suma total de la nota, solo la nota de cada requerimiento.

        Lista de oportunidades de mejora más allá de los requerimientos.

        El código del estudiante es: {desarrollo}.
        """


        print("si funciona ")

        # Crear un thread con el archivo adjunto
        thread = client.beta.threads.create(
            messages=[
                {
                    "role": "user",
                    "content": f"{prompt}",
                    
                }
            ]
        )
        
        # Ejecutar el thread y esperar su finalización
        run = client.beta.threads.runs.create(thread_id=thread.id, assistant_id=assistant_id)
        
        while run.status not in ["completed", "failed"]:
            run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            print(run.status)
            await asyncio.sleep(1)
        
        # Obtener los mensajes del thread
        messages = client.beta.threads.messages.list(thread_id=thread.id)
        mensaje = mostrar_mensajes_assistant(messages)
        print("mensaje",mensaje)
        # Limpiar recursos: borrar thread y archivo subido
        client.beta.threads.delete(thread_id=thread.id)
        return mensaje
    except Exception as e:
        print(f"Error: {e}")

###################################
###   Generacion de Preguntas   ###
###################################



@router.post("/crear-preguntas/{assistant_id}")
async def crear_preguntas(assistant_id: str, vf: str, desarrollo: str, alternativas: str, dificultad: str):
    print('asistente :' + assistant_id)

    # Definir el prompt
    prompt = f'''Generame preguntas segun su cantidad y tipo que seran indicadas a continuacion. Las preguntas deben basarse exclusivamente en la información contenida en los archivos proporcionados en el vector_store, pero sin mencionar los nombres de los documentos. Cada pregunta debe abordar un concepto aprendido en los archivos. Las preguntas deben tener una dificultad {dificultad}, cada tipo de pregunta deberá seguir el siguiente formato: 

    1. **Tipo: Verdadero o Falso** deben ser {vf} preguntas
    Pregunta: Debe comenzar con "Pregunta_vf:" seguida del enunciado.
    Alternativa correcta: Debe ser indicada con "Alternativa correcta:" seguida de "V" para Verdadero o "F" para Falso.

    2. **Tipo: Desarrollo** deben ser {desarrollo} preguntas
    Pregunta: Debe comenzar con "Pregunta_desarrollo:" seguida del enunciado.
    Respuesta: Debe comenzar con "Respuesta:" seguida de una breve respuesta.

    3. **Tipo: Alternativas** deben ser {alternativas} preguntas
    Pregunta: Debe comenzar con "Pregunta_alternativas:" seguida del enunciado de la pregunta.
    Alternativas: Cada alternativa debe estar en una nueva línea, comenzando con una letra en minúscula seguida de un paréntesis, por ejemplo, "a)", "b)", hasta la "e)", y luego el texto de la alternativa.
    Alternativa correcta: Debe comenzar con "Alternativa correcta:" seguida de la letra correspondiente a la opción correcta (en minúscula).

    Utiliza un tono formal y no incluyas introducciones ni comentarios adicionales ,no menciones explícitamente los documentos en las preguntas, solo proporciona la lista anidada. No incluyas formatos especiales como **, - ,o markdown en general, solamente devuelve texto plano. Si la cantidad de preguntas es 0 no generes ese tipo de preguntas'''

    max_retries = 4  # Número máximo de reintentos
    retries = 0
    thread_retries = 0
    print(f"Prompt generado: {prompt}")
    print('generando pruebas con assistant: '+ assistant_id)
    try:
        while retries < max_retries:
            if thread_retries == 0 or thread_retries >= 2:
                # Crear hilo inicial
                thread = client.beta.threads.create(
                    messages=[{"role": "user", "content": prompt}],
                )
                thread_retries = 0

            run = client.beta.threads.runs.create(
                thread_id=thread.id,
                assistant_id=assistant_id
            )

            # Esperar a que la ejecución se complete
            while run.status not in ["completed", "failed"]:
                run = client.beta.threads.runs.retrieve(
                    thread_id=thread.id,
                    run_id=run.id
                )
                print(run.status)
                await asyncio.sleep(1)

            # Obtener los mensajes del hilo
            messages = client.beta.threads.messages.list(
                thread_id=thread.id,
            )

            # Interpretar el contenido del mensaje
            preguntas = obtener_mensaje_del_run(messages, run.id)
            #print("PRIMERA GENERACION:"+ preguntas)
            # Verificar si se generaron preguntas válidas con los prefijos actualizados
            if preguntas and re.search(r"(Pregunta_vf:|Pregunta_desarrollo:|Pregunta_alternativas:)", preguntas):
                print("PREGUNTAS ACAAA: " +  preguntas)
                return preguntas, thread.id

            print("No se encontraron preguntas válidas, enviando el prompt nuevamente...")
            client.beta.threads.messages.create(
                thread_id=thread.id,
                content=prompt,
                role="user"
            )

            retries += 1
            thread_retries += 1

    except Exception as e:
        print("Error generando pregunta de alternativas: ", e)
        return {"error": "Fallo generando pregunta de alternativas:"}

    return {"error": "No se pudo generar preguntas de alternativas tras varios intentos."}


@router.post("/regenerar-preguntas/{assistant_id}")
async def regenerar_preguntas(assistant_id: str, thread_id: str, pregunta_tipo: str):
    # Definir el prompt basado en el tipo de pregunta a regenerar
    if pregunta_tipo == "vf":
        prompt = '''Generame 1 pregunta del tipo Verdadero o Falso DIFERENTE a las anteriores.SOLO ENTREGA ESTA PREGUNTA Y NINGUNA OTRA MAS.
        Pregunta: Debe comenzar con "Pregunta_vf:" seguida del enunciado.
        Alternativa correcta: Debe ser indicada con "Alternativa correcta:" seguida de "V" para Verdadero o "F" para Falso.
        No incluyas otros comentarios ni introducciones, solo la pregunta nueva y la respuesta. NO vuelvas a incluir las preguntas anteriormente generadas'''
    elif pregunta_tipo == "desarrollo":
        prompt = '''Generame 1 pregunta del tipo Desarrollo DIFERENTE a las anteriores.SOLO ENTREGA ESTA PREGUNTA Y NINGUNA OTRA MAS.
        Pregunta: Debe comenzar con "Pregunta_desarrollo:" seguida del enunciado.
        Respuesta: Debe comenzar con "Respuesta:" seguida de una breve respuesta.
        No incluyas otros comentarios ni introducciones, solo la pregunta nueva y la respuesta. NO vuelvas a incluir las preguntas anteriormente generadas'''
    elif pregunta_tipo == "alternativa":
        prompt = '''Generame 1 pregunta del tipo Alternativas DIFERENTE a las anteriores. SOLO ENTREGA ESTA PREGUNTA Y NINGUNA OTRA MAS.
        Pregunta: Debe comenzar con "Pregunta_alternativas:" seguida del enunciado de la pregunta.
        Alternativas: Cada alternativa debe estar en una nueva línea, comenzando con una letra en minúscula seguida de un paréntesis, por ejemplo, "a)", "b)", hasta la "e)", y luego el texto de la alternativa.
        Alternativa correcta: Debe comenzar con "Alternativa correcta:" seguida de la letra correspondiente a la opción correcta (en minúscula).
        No incluyas otros comentarios ni introducciones, solo la pregunta nueva y las alternativas. NO vuelvas a incluir las preguntas anteriormente generadas'''
    else:
        return {"error": "Tipo de pregunta inválido"}

    max_retries = 4  # Número máximo de reintentos
    retries = 0
    print(f"Prompt generado: {prompt}")

    try:
        # Crear hilo inicial metodo parche
        thread = client.beta.threads.create(
            messages=[{"role": "user", "content": prompt}],
        )
        while retries < max_retries:
            # Ejecutar el hilo ya existente
            run = client.beta.threads.runs.create(
                thread_id=thread.id,
                assistant_id=assistant_id
            )

            # Esperar a que la ejecución se complete
            while run.status not in ["completed", "failed"]:
                run = client.beta.threads.runs.retrieve(
                    thread_id=thread.id,
                    run_id=run.id
                )
                print(run.status)
                await asyncio.sleep(1)

            # Obtener los mensajes del hilo para obtener las preguntas generadas
            messages = client.beta.threads.messages.list(
                thread_id=thread.id,
            )

            # Interpretar el contenido del mensaje
            preguntas = obtener_mensaje_del_run(messages, run.id)
            # Verificar si se generaron preguntas válidas con los prefijos correctos
            if preguntas and re.search(r"(Pregunta_vf:|Pregunta_desarrollo:|Pregunta_alternativas:)", preguntas):
                print("Pregunta regenerada correctamente: " + preguntas)
                return preguntas

            print("No se encontraron preguntas válidas, enviando el prompt nuevamente...")
            client.beta.threads.messages.create(
                thread_id=thread.id,
                content=prompt,
                role="user"
            )

            retries += 1

    except Exception as e:
        print("Error regenerando la pregunta: ", e)
        return {"error": f"Fallo regenerando pregunta: {e}"}

    return {"error": "No se pudo regenerar la pregunta tras varios intentos."}



#########################################
###  Generacion De Guiones de Clases  ###
#########################################
async def esperar_run_completado(thread_id, run_id, timeout=120):
    """
    Espera activamente a que un run termine, con timeout
    """
    start_time = time.time()
    
    while True:
        try:
            run = client.beta.threads.runs.retrieve(
                thread_id=thread_id,
                run_id=run_id
            )
            
            print(f"⏳ Estado actual del run: {run.status}")
            
            if run.status in ["completed", "failed", "cancelled", "expired"]:
                return run
                
            if time.time() - start_time > timeout:
                print(f"❌ Timeout después de {timeout} segundos")
                return None
                
            await asyncio.sleep(2)
            
        except Exception as e:
            print(f"❌ Error consultando run: {e}")
            await asyncio.sleep(2)





@router.post("/crear_guion/{assistant_id}")
async def crear_guion(
    assistant_id: str,
    titulo: str = Form(...),
    resultado_aprendizaje: str = Form(...),
    contenido_tematico: str = Form(...),
    tipo_clase: str = Form(...),
    duracion: int = Form(...),
    semana: int = Form(...),
    vector_id: str = Form(...),
):
    print("✅ Datos recibidos correctamente en la API.")

    # 🔗 Vincular assistant al vector store
    try:
        client.beta.assistants.update(
            assistant_id=assistant_id,
            tools=[{"type": "file_search"}],
            tool_resources={"file_search": {"vector_store_ids": [vector_id]}},
        )
        print("🔗 Assistant vinculado al vector store:", vector_id)
    except Exception as e:
        print("⚠️ Error vinculando vector:", e)
        # seguimos igual, solo que sin corpus

    # Crear thread único para TODO el proceso (2 pasos)
    thread = client.beta.threads.create()
    print(f"🧵 Thread creado: {thread.id}")

    try:
        # ================================================================
        # PASO 1: ANÁLISIS SIMPLE DEL RA (INTERNO / NO PARA CONFUNDIR DOCENTE)
        # ================================================================
        print("🧠 PASO 1: Analizando RA para guiar el guion...")

        prompt_analisis_ra = f"""
Eres un asistente de apoyo docente (educación superior, área informática).
Tu objetivo es SOLO ayudar a que el guion quede alineado al Resultado de Aprendizaje.

Analiza este RA y devuelve un JSON simple con campos EXACTOS:

TÍTULO: {titulo}
RA: "{resultado_aprendizaje}"
DURACIÓN: {duracion} minutos

FORMATO JSON:
{{
  "analisis_ra": {{
    "verbos_clave": ["verbo1", "verbo2"],
    "que_debe_lograr_el_estudiante": "Explicación corta en lenguaje simple",
    "evidencias_esperadas": ["Evidencia observable 1", "Evidencia observable 2"],
    "enfoque_en_una_sesion": "Cómo acotar el RA a esta clase en términos prácticos"
  }}
}}

REGLAS:
- Lenguaje AMIGABLE para docentes (sin teorías, sin jerga).
- Realista para {duracion} minutos.
- DEVUELVE SOLO EL JSON (sin texto adicional).
"""
        resultado_fase1 = await llamada_ia_estructurada(
            thread_id=thread.id,
            assistant_id=assistant_id,
            prompt=prompt_analisis_ra,
            nombre_fase="analisis_ra",
            estructura_esperada={
                "analisis_ra": {
                    "verbos_clave": list,
                    "que_debe_lograr_el_estudiante": str,
                    "evidencias_esperadas": list,
                    "enfoque_en_una_sesion": str,
                }
            },
        )

        if isinstance(resultado_fase1, dict) and "error" in resultado_fase1:
            raise Exception(f"Error en análisis RA: {resultado_fase1['error']}")

        analisis_ra = resultado_fase1.get("analisis_ra", resultado_fase1)
        verbos_clave = analisis_ra.get("verbos_clave", [])
        verbos_str = ", ".join(verbos_clave) if verbos_clave else ""

        # ================================================================
        # PASO 2: GUION COMPLETO (FORMATO ANTIGUO, LENGUAJE DOCENTE)
        # ================================================================
        print("🎯 PASO 2: Generando guion de clase (formato docente)...")

        prompt_guion = f"""
Actúa como un Asistente Inteligente de Diseño Instruccional especializado en educación superior en el área de INFORMÁTICA.

INSUMOS DEL DOCENTE:
• Título de la clase: {titulo}
• Resultado de Aprendizaje: {resultado_aprendizaje}
• Contenido temático: {contenido_tematico}
• Estilo de clase: {tipo_clase}
• Duración total: {duracion} minutos
• Semana del semestre: {semana}
• Recursos disponibles: material del corpus cargado por el docente (si existe).

APOYO INTERNO (para mantener alineación, no lo expliques):
- Verbos clave del RA: {verbos_str}
- Qué debe lograr el estudiante: {analisis_ra.get("que_debe_lograr_el_estudiante","")}
- Evidencias esperadas: {", ".join(analisis_ra.get("evidencias_esperadas", []))}
- Enfoque en una sesión: {analisis_ra.get("enfoque_en_una_sesion","")}

OBJETIVOS PRINCIPALES:
1. Generar una secuencia didáctica completa (inicio-desarrollo-cierre)
2. Diseñar evaluaciones formativas BREVES y PRÁCTICAS para cada momento
3. Todo debe estar explícitamente alineado al Resultado de Aprendizaje

SECUENCIA DIDÁCTICA - DISTRIBUCIÓN SUGERIDA PARA {duracion} MINUTOS:
- INICIO (15-20%): Activación de conocimientos previos y contextualización
- DESARROLLO (60-70%): Actividades principales de aprendizaje
- CIERRE (15-20%): Síntesis y verificación del aprendizaje

🎯 EVALUACIONES FORMATIVAS (elige 1 por momento):
INICIO (elige 1):
• Pregunta detonante
• Mini-quiz de comprensión inicial
• Verdadero/Falso con justificación breve
• Identificación de errores conceptuales

DESARROLLO (elige 1):
• Pregunta de aplicación breve
• Mini-caso o situación problema
• Completación de un paso en un proceso
• Identificación de errores en código/proceso

CIERRE (elige 1):
• Ticket de salida
• Reflexión guiada de 1 minuto (1-minute paper)
• Mapa mental / esquema rápido
• Pregunta de síntesis conceptual

REGLAS PARA EVALUACIONES:
- Deben ser CORTAS (1-3 ítems máximo)
- Ejecución rápida (2-5 minutos cada una)
- Alineadas explícitamente al RA
- Basadas en contenido de la clase
- Permitir retroalimentación inmediata
- Priorizar comprensión, aplicación y análisis (Bloom) SIN mencionar Bloom en el texto al docente

🚨 ESPECIFICACIONES PARA INFORMÁTICA:
- Prioriza ejercicios de código, análisis de algoritmos o diseño de sistemas
- Usa terminología técnica apropiada pero clara
- Considera la duración real de {duracion} minutos

IMPORTANTE:
- CALCULA LOS TIEMPOS EN MINUTOS PARA CADA SECCIÓN BASÁNDOTE EN {duracion} MINUTOS.
- DEVUELVE ÚNICAMENTE JSON, SIN TEXTO ADICIONAL.
- Usa ÚNICAMENTE comillas rectas (") en el JSON.

FORMATO JSON REQUERIDO:
{{
  "identificacion_clase": {{
    "nombre_asignatura": "",
    "unidad_semana_clase": "Semana {semana}",
    "duracion_sesion": "{duracion} minutos",
    "resultado_aprendizaje": "{resultado_aprendizaje}",
    "contenidos_clase": "{contenido_tematico}"
  }},
  "secuencia_actividades": {{
    "inicio": {{
      "proposito_pedagogico": "",
      "pregunta_gatillante": "",
      "actividad_principal": "",
      "tiempo_estimado": "",
      "pasos_docente": [],
      "pasos_estudiantes": []
    }},
    "desarrollo": {{
      "proposito_pedagogico": "",
      "exposicion_guiada": "",
      "actividades_principales": [],
      "discusiones_debates": "",
      "recursos_desarrollo": [],
      "tiempo_estimado": "",
      "pasos_docente": [],
      "pasos_estudiantes": []
    }},
    "cierre": {{
      "proposito_pedagogico": "",
      "sintesis_clase": "",
      "actividad_integradora": "",
      "tarea_siguiente_clase": "",
      "tiempo_estimado": "",
      "pasos_docente": [],
      "pasos_estudiantes": []
    }}
  }},
  "evaluaciones_formativas": [
    {{
      "momento": "inicio",
      "proposito": "Qué busca evaluar y cómo se alinea al RA",
      "tipo": "pregunta_detonante | mini_quiz | verdadero_falso | identificacion_errores",
      "actividad": "Descripción específica de la evaluación (1-3 ítems máximo)",
      "duracion_estimada": "",
      "criterio_observacion": "Qué evidencia debe producir el estudiante",
      "retroalimentacion_sugerida": "Cómo retroalimentar según las respuestas"
    }},
    {{
      "momento": "desarrollo",
      "proposito": "Qué busca evaluar y cómo se alinea al RA",
      "tipo": "pregunta_aplicacion | mini_caso | completacion_paso | identificacion_errores",
      "actividad": "Descripción específica de la evaluación (1-3 ítems máximo)",
      "duracion_estimada": "",
      "criterio_observacion": "Qué evidencia debe producir el estudiante",
      "retroalimentacion_sugerida": "Cómo retroalimentar según las respuestas"
    }},
    {{
      "momento": "cierre",
      "proposito": "Qué busca evaluar y cómo se alinea al RA",
      "tipo": "ticket_salida | reflexion_guiada | mapa_mental | pregunta_sintesis",
      "actividad": "Descripción específica de la evaluación (1-3 ítems máximo)",
      "duracion_estimada": "",
      "criterio_observacion": "Qué evidencia debe producir el estudiante",
      "retroalimentacion_sugerida": "Cómo retroalimentar según las respuestas"
    }}
  ],
  "estrategias_didacticas": [
    {{
      "tipo": "Clase magistral breve|Aprendizaje basado en problemas|Aprendizaje activo|Trabajo colaborativo|Aprendizaje Basado en Casos|Microexplicaciones|Gamificacion|Simulacion",
      "nombre": "",
      "descripcion": "",
      "alineacion_ra": ""
    }}
  ],
  "bibliografia_material": [
    {{
      "tipo": "BIBLIOGRAFIA|MATERIAL_COMPLEMENTARIO",
      "referencia": "",
      "uso_recomendado": ""
    }}
  ]
}}


REGLAS PARA ESTRATEGIAS DIDÁCTICAS (OBLIGATORIO):

- Devuelve EXACTAMENTE 3 objetos dentro del arreglo "estrategias_didacticas".
- Cada objeto debe representar un momento distinto de la clase.
- El campo "nombre" DEBE comenzar obligatoriamente con uno de los siguientes prefijos:
  "INICIO –", "DESARROLLO –", "CIERRE –".
- El campo "tipo" debe contener SOLO UNA opción exacta de la lista permitida.
- El campo "descripcion" debe escribirse como una receta breve y concreta, con pasos numerados:
  Ejemplo: "1) Acción docente. 2) Acción estudiante. 3) Cierre rápido."
- Usar lenguaje directo, operativo y aplicable en aula.
- Prohibido lenguaje teórico, abstracto o pedagógico avanzado.
- El campo "alineacion_ra" debe ser UNA sola frase clara que explique cómo la estrategia contribuye al logro del Resultado de Aprendizaje.
- Si no devuelves exactamente 3 estrategias, la respuesta se considera inválida.

"""
        resultado_guion = await llamada_ia_estructurada(
            thread_id=thread.id,
            assistant_id=assistant_id,
            prompt=prompt_guion,
            nombre_fase="guion_clase",
            estructura_esperada={
                "identificacion_clase": dict,
                "secuencia_actividades": dict,
                "evaluaciones_formativas": list,
                "estrategias_didacticas": list,
                "bibliografia_material": list,
            },
        )

        if isinstance(resultado_guion, dict) and "error" in resultado_guion:
            raise Exception(f"Error generando guion: {resultado_guion['error']}")

        print("\n📘 GUION DE CLASE GENERADO")
        print("=" * 80)

        print("\n🔹 IDENTIFICACIÓN DE LA CLASE")
        print(json.dumps(resultado_guion.get("identificacion_clase", {}), indent=2, ensure_ascii=False))

        print("\n🔹 SECUENCIA DIDÁCTICA")
        print(json.dumps(resultado_guion.get("secuencia_actividades", {}), indent=2, ensure_ascii=False))

        print("\n🔹 EVALUACIONES FORMATIVAS")
        print(json.dumps(resultado_guion.get("evaluaciones_formativas", []), indent=2, ensure_ascii=False))

        print("\n🔹 ESTRATEGIAS DIDÁCTICAS")
        print(json.dumps(resultado_guion.get("estrategias_didacticas", []), indent=2, ensure_ascii=False))

        print("\n🔹 BIBLIOGRAFÍA Y MATERIAL")
        print(json.dumps(resultado_guion.get("bibliografia_material", []), indent=2, ensure_ascii=False))

        print("=" * 80)

        # ================================================================
        # RESPUESTA FINAL (DOCENTE)
        # - Devuelvo el guion (formato antiguo)
        # - y el analisis_ra SOLO si quieres guardarlo (puedes ocultarlo en UI)
        # ================================================================
        return {
            **resultado_guion,
            "analisis_ra": analisis_ra,  # si no quieres exponerlo, elimínalo o escóndelo en frontend
            "metadata": {
                "thread_id": thread.id,
                "duracion_minutos": duracion,
                "fecha_generacion": datetime.now().isoformat(),
                "version_guion": "v1.1-2pasos-docente",
                "fases_completadas": ["analisis_ra_simple", "guion_formato_docente"],
            },
            "thread_id": thread.id,
        }

    except Exception as e:
        print(f"❌ ERROR CRÍTICO: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            "error": f"Error generando guion: {str(e)}",
            "thread_id": thread.id if "thread" in locals() else None,
        }

async def llamada_ia_estructurada(thread_id, assistant_id, prompt, nombre_fase, estructura_esperada=None, timeout=60):
    print(f"  📤 Enviando {nombre_fase} ({len(prompt)} caracteres)")

    try:
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=prompt
        )

        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=assistant_id,
        )

        completed_run = await esperar_run_completado(thread_id, run.id, timeout)

        if not completed_run or completed_run.status != "completed":
            status = completed_run.status if completed_run else "timeout"
            error_msg = f"Run no completado en {nombre_fase}: {status}"
            print(f"  ❌ {error_msg}")
            return {"error": error_msg}

        messages = client.beta.threads.messages.list(thread_id=thread_id)
        respuesta_texto = obtener_mensaje_del_run(messages, completed_run.id)

        if not respuesta_texto:
            error_msg = f"Respuesta vacía en {nombre_fase}"
            print(f"  ❌ {error_msg}")
            return {"error": error_msg}

        print(f"  🔍 Respuesta recibida ({len(respuesta_texto)} chars): {respuesta_texto[:200]}...")

        respuesta_json = extraer_json_del_texto(respuesta_texto)

        if not respuesta_json:
            print(f"  ❌ No se pudo extraer JSON en {nombre_fase}")
            return {"error": f"No se pudo extraer JSON válido en {nombre_fase}"}

        # ✅ Normalización: si viene envuelto por nombre_fase, desenvuelve
        # Ej: {"analisis_ra": {...}} o {"guion_clase": {...}}
        if isinstance(respuesta_json, dict) and nombre_fase in respuesta_json and isinstance(respuesta_json[nombre_fase], (dict, list)):
            respuesta_json = respuesta_json[nombre_fase]

        # ✅ (Opcional) Validación muy ligera si te sirve:
        if isinstance(estructura_esperada, dict) and isinstance(respuesta_json, dict):
            claves_faltantes = [k for k in estructura_esperada.keys() if k not in respuesta_json]
            if claves_faltantes:
                print(f"  ⚠️ {nombre_fase}: faltan claves {claves_faltantes}")

        print(f"  ✅ {nombre_fase} completado exitosamente")
        return respuesta_json

    except Exception as e:
        error_msg = f"Error en llamada_ia_estructurada ({nombre_fase}): {str(e)}"
        print(f"  ❌ {error_msg}")
        import traceback
        traceback.print_exc()
        return {"error": error_msg}

def extraer_json_robusto(texto):
    """Extrae JSON incluso si viene con texto adicional o formato incorrecto"""
    if not texto:
        return None
    
    # 1. Intentar extraer con tu función existente
    try:
        json_obj = extraer_json_del_texto(texto)
        if json_obj:
            return json_obj
    except:
        pass
    
    # 2. Buscar entre ```json ``` o ``` ```
    import re
    json_patterns = [
        r'```json\s*(\{.*?\})\s*```',  # ```json { ... } ```
        r'```\s*(\{.*?\})\s*```',      # ``` { ... } ```
        r'```(\{.*?\})```',            # ```{ ... }```
        r'(\{.*\})',                   # Cualquier JSON
    ]
    
    for pattern in json_patterns:
        match = re.search(pattern, texto, re.DOTALL)
        if match:
            json_str = match.group(1).strip()
            try:
                # Limpiar caracteres problemáticos
                json_str = json_str.replace('\n', ' ').replace('\t', ' ')
                return json.loads(json_str)
            except json.JSONDecodeError:
                continue
    
    # 3. Si aún no funciona, intentar encontrar el primer { y último }
    start = texto.find('{')
    end = texto.rfind('}') + 1
    
    if start >= 0 and end > start:
        json_str = texto[start:end]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
    
    return None
def imprimir_resultado_final(guion_final: str):
    """Imprime el resultado final formateado"""
    print("\n" + "="*80)
    print("🎬 GUION FINAL COMPLETO")
    print("="*80)
    
    # Intentar extraer JSON si existe
    try:
        # Buscar contenido entre { y }
        start = guion_final.find('{')
        end = guion_final.rfind('}') + 1
        
        if start != -1 and end != -1:
            json_str = guion_final[start:end]
            # Parsear para verificar si es JSON válido
            import json
            json_obj = json.loads(json_str)
            print("✅ JSON válido detectado")
            print(f"📊 Estructura: {list(json_obj.keys())}")
            print("\n📋 Contenido completo:")
            print(json.dumps(json_obj, indent=2, ensure_ascii=False))
        else:
            print("📄 Contenido completo (texto):")
            print(guion_final)
    except:
        print("📄 Contenido completo:")
        print(guion_final)
    
    print("="*80)
    print(f"📏 Longitud total: {len(guion_final)} caracteres")


def extraer_json_del_texto(texto):
    if not texto:
        return None

    texto = texto.strip()

    # 1. Intento directo
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass

    # 2. Buscar desde el primer { hasta el último }
    inicio = texto.find("{")
    fin = texto.rfind("}")

    if inicio == -1 or fin == -1 or fin <= inicio:
        return None

    candidato = texto[inicio:fin + 1]

    try:
        return json.loads(candidato)
    except json.JSONDecodeError as e:
        print("❌ JSON inválido, NO se rescata parcialmente")
        print("🔎 Error:", e)
        print("🔎 Inicio del JSON:")
        print(candidato[:500])
        return None

def buscar_json_especifico(texto):
    """Busca estructuras JSON específicas en el texto"""
    # Buscar el JSON que contiene TODOS los campos que necesitamos
    patrones = [
        r'\{[^{}]*"evaluaciones_formativas"[^{}]*"frameworks"[^{}]*"materiales_apoyo"[^{}]*\}',
        r'\{.*"frameworks".*\}',
        r'\{.*"evaluaciones_formativas".*\}',
    ]
    
    for patron in patrones:
        match = re.search(patron, texto, re.DOTALL)
        if match:
            json_str = match.group(0)
            try:
                return json.loads(json_str)
            except:
                continue
    
    return None
def limpiar_caracteres_json(texto):
    """Limpia caracteres problemáticos en JSON"""
    import re
    
    # Reemplazar caracteres de control (excepto \t, \n, \r en strings)
    # Primero, proteger strings entre comillas
    def proteger_strings(match):
        contenido = match.group(0)
        # Escapar caracteres problemáticos dentro del string
        contenido = contenido.replace('\n', '\\n')
        contenido = contenido.replace('\r', '\\r')
        contenido = contenido.replace('\t', '\\t')
        return contenido
    
    # Proteger contenido entre comillas dobles
    texto = re.sub(r'"(.*?)"', lambda m: f'"{m.group(1).replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")}"', texto, flags=re.DOTALL)
    
    # Remover caracteres de control fuera de strings
    texto = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', texto)
    
    return texto

def limpiar_json_agresivamente(texto):
    """Limpieza más agresiva para JSON problemático"""
    import json
    import re
    
    # 1. Encontrar y corregir strings mal formadas
    lines = texto.split('\n')
    cleaned_lines = []
    
    for line in lines:
        # Buscar strings que cruzan múltiples líneas
        line = line.strip()
        if line:
            # Escapar comillas dobles dentro de strings
            in_string = False
            result = []
            i = 0
            while i < len(line):
                char = line[i]
                if char == '"' and (i == 0 or line[i-1] != '\\'):
                    in_string = not in_string
                    result.append(char)
                elif in_string and char in ['\n', '\r', '\t']:
                    # Escapar caracteres de control dentro de strings
                    escape_map = {'\n': '\\n', '\r': '\\r', '\t': '\\t'}
                    result.append(escape_map.get(char, char))
                else:
                    result.append(char)
                i += 1
            cleaned_lines.append(''.join(result))
    
    texto = '\n'.join(cleaned_lines)
    
    # 2. Intentar reparar JSON incompleto
    # Contar llaves para ver si está balanceado
    open_braces = texto.count('{')
    close_braces = texto.count('}')
    
    if open_braces > close_braces:
        # Agregar llaves de cierre faltantes
        texto += '}' * (open_braces - close_braces)
    elif close_braces > open_braces:
        # Remover llaves de cierre extras al final
        while close_braces > open_braces and texto.endswith('}'):
            texto = texto[:-1]
            close_braces -= 1
    
    return texto

@router.post("/generar_resumen/{assistant_id}")
async def generar_resumen_api(
    assistant_id: str,
    thread_id: str = Form(...),
    vector_id: str = Form(...)
):
    print(f"🧠 Generando resumen para assistant_id={assistant_id}, thread_id={thread_id}, vector_id={vector_id}")
    
    prompt = """Eres un asistente educativo experto en análisis de textos académicos, síntesis conceptual y diseño instruccional.

Tu tarea es analizar TODO el contenido disponible en el vector_store (no solo este thread) y generar un RESUMEN PROFUNDO, PRECISO y PEDAGÓGICAMENTE ÚTIL para un docente.

REGLAS ESTRICTAS:
- NO incluyas citas de fuentes como 【4:13†source】 en el resumen final
- NO uses marcadores de referencia ni notaciones de fuente
- Los conceptos clave deben incluir una breve descripción o definición
- Extrae la información sustancial pero preséntala en formato limpio, sin referencias
- Usa únicamente el contenido verificado del vector_store, pero no muestres las fuentes

DIMENSIONES QUE EL RESUMEN DEBE CUBRIR:
1. Tema principal → el foco central del corpus
2. Ideas principales → 4-7 puntos estructurales del tema  
3. Conceptos clave → términos esenciales con su significado/definición
4. Conclusión → síntesis integradora con relevancia educativa

FORMATO DE SALIDA (JSON):
{
  "tema_principal": "Texto descriptivo del tema central",
  "ideas_principales": [
    "Idea 1 - descripción completa",
    "Idea 2 - descripción completa",
    "Idea 3 - descripción completa"
  ],
  "conceptos_clave": [
    "Concepto 1: definición o descripción breve",
    "Concepto 2: definición o descripción breve", 
    "Concepto 3: definición o descripción breve"
  ],
  "conclusion": "Texto de síntesis final"
}

IMPORTANTE: Los conceptos clave deben seguir el formato "Concepto: descripción" para que sean útiles para el docente."""

    try:
        # Enviar el prompt dentro del mismo thread
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=prompt
        )

        # Crear run
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=assistant_id,
        )
        
        # ✅ OPCIÓN MÁS LIMPIA: Usar tu función esperar_run_completado
        completed_run = await esperar_run_completado(thread_id, run.id, timeout=90)

        if not completed_run or completed_run.status != "completed":
            return {"error": "El run no completó correctamente"}

        # Obtener mensajes del hilo
        messages = client.beta.threads.messages.list(thread_id=thread_id)
        resumen = obtener_mensaje_del_run(messages, completed_run.id)
        print("resumen : ", resumen)
        if not resumen or len(resumen.strip()) < 20:
            print("⚠️ No se obtuvo resumen válido del modelo.")
            return {"error": "No se pudo generar un resumen válido."}

        print("✅ Resumen generado correctamente.")
        return {"resumen": resumen, "thread": thread_id}

    except Exception as e:
        print("❌ Error generando resumen:", e)
        return {"error": f"Fallo generando resumen: {str(e)}"}



@router.post("/generar_mapa_conceptual/{assistant_id}")
async def generar_mapa_conceptual_api(
    assistant_id: str,
    thread_id: str = Form(...),
    vector_id: str = Form(...),
    titulo_guion: str = Form(...)
):
    print(f"🧠 Generando mapa conceptual (3 fases pedagogicas) para assistant_id={assistant_id}")

    try:
        # -----------------------------------------------------------------------
        # 🧹 1. Esperar cualquier run previo activo
        # -----------------------------------------------------------------------
        active_runs = client.beta.threads.runs.list(thread_id=thread_id)
        for run in active_runs:
            if run.status in ["queued", "in_progress"]:
                print(f"⏳ Esperando run previo: {run.id}")
                await esperar_run_completado(thread_id, run.id)

        # -----------------------------------------------------------------------
        # 📘 FASE 1 → EXTRACCIÓN PEDAGÓGICA DEL CONTENIDO
        # -----------------------------------------------------------------------
        prompt_fase1 = f"""
Fase 1 — EXTRACCIÓN PEDAGÓGICA DEL CONTENIDO

Eres un experto en pedagogía, aprendizaje significativo (Novak), ciencias cognitivas y diseño instruccional.

Analiza TODO el contenido del thread aplicando metodología pedagógica:

METODOLOGÍA PEDAGÓGICA:
1. Análisis exhaustivo del corpus - Identifica el tema central y componentes principales
2. Identificación conceptual - Selecciona conceptos nucleares, evitando redundancias
3. Jerarquización - Organiza desde lo general a lo específico

EXTRACCIÓN ESTRUCTURADA:
• Tema central (futuro cp_1)
• 3-5 conceptos principales nucleares 
• 2-4 conceptos secundarios por cada principal 
• 0-3 conceptos terciarios por cada secundario 
• Relaciones lógicas significativas entre conceptos
• Glosario técnico esencial del material

REGLAS PEDAGÓGICAS:
- Enfócate en conceptos, no en procedimientos
- Prioriza diferenciación progresiva (general → específico)
- Identifica relaciones para reconciliación integradora
- Elimina ambigüedades conceptuales
REGLA CRÍTICA:
- Los nombres de conceptos deben ser reutilizables literalmente en fases posteriores.
- Evita sinónimos creativos: usa una denominación estable por concepto.
Formato de salida: texto estructurado claro, sin JSON todavía.
REGLA DE NIVEL CONCEPTUAL:
- Un concepto PRINCIPAL debe poder existir como categoría autónoma.
- Si un término describe una PROPIEDAD de otro concepto,
  debe ubicarse como SECUNDARIO o TERCIARIO.
Ejemplo:
✓ "Sistemas Organizacionales" (concepto)
✓ "Estructura del Sistema" (atributo → secundario)
✗ "Estructura" como principal aislado

"""

        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=prompt_fase1
        )

        run1 = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=assistant_id
        )

        completed_run1 = await esperar_run_completado(thread_id, run1.id)

        fase1_data = obtener_mensaje_del_run(
            client.beta.threads.messages.list(thread_id=thread_id),
            completed_run1.id
        )

        if not fase1_data:
            return {"error": "Fase 1 no devolvió información"}
        print("✅ Fase 1 completada")
        print("📄 Fase 1 output (primeros 500 chars):")
        print(fase1_data[:50000])
        print("-" * 80)
        print("✅ Fase 1 pedagógica completada")

        # -----------------------------------------------------------------------
        # 🟦 FASE 1.5 → ORGANIZACIÓN JERÁRQUICA PEDAGÓGICA
        # -----------------------------------------------------------------------
        prompt_fase15 = f"""
Fase 1.5 — ORGANIZACIÓN JERÁRQUICA PEDAGÓGICA (VERSIÓN CONTROLADA)

Tu objetivo es ORGANIZAR jerárquicamente los conceptos extraídos en la Fase 1,
SIN reinterpretar, renombrar arbitrariamente ni eliminar profundidad existente.

══════════════════════════════════════
FILTRO CONCEPTUAL ESTRICTO (APLICAR PRIMERO)
══════════════════════════════════════

CONCEPTOS VÁLIDOS (✓):
- Sustantivos o frases nominales
- Términos técnicos o teóricos
- Entidades conceptuales definibles
- Categorías con contenido propio

CONCEPTOS INVÁLIDOS (✗ ELIMINAR O REEMPLAZAR):
- Frases verbales ("definir", "comparar", "aplicar")
- Referencias a acciones o procedimientos
- Términos vagos ("claves", "relevancia", "importancia")
- Menciones a "ejemplos" o "casos"

EJEMPLOS DE TRANSFORMACIÓN:
Antes: "Definición y comprensión" ✗
Después: "Principios Fundamentales" ✓

Antes: "Ejemplos de aplicación" ✗  
Después: "Dominios de Aplicación" ✓

Antes: "Claves para la comprensión" ✗
Después: "Dimensiones Analíticas" ✓

══════════════════════════════════════
INSTRUCCIÓN DE ORGANIZACIÓN
══════════════════════════════════════

1. Aplica el filtro conceptual a TODOS los conceptos.
2. - Reemplaza conceptos inválidos SOLO si son formalmente inválidos
  (verbales, procedimentales, vagos),
  preservando estrictamente el significado original.
- NO introducir nuevos dominios conceptuales.

3. SOLO DESPUÉS organiza jerárquicamente.

JERARQUÍA PEDAGÓGICA BASE:
• Nivel 1: Concepto raíz (1 único)
• Nivel 2: Conceptos principales (3–5, según complejidad real)
• Nivel 3: Conceptos secundarios (2–4 por principal)
• Nivel 4: Conceptos terciarios (0–3 por secundario)

🎯 PRINCIPIO GUÍA:
LA ESTRUCTURA DEBE SEGUIR AL CONTENIDO, NO AL REVÉS.

══════════════════════════════════════
🔒 REGLAS CRÍTICAS DE ESTABILIDAD
══════════════════════════════════════

1. El CONCEPTO RAÍZ debe ser EXACTAMENTE el Tema Central de la Fase 1.
2. El Concepto Raíz NO puede reaparecer como principal, secundario o terciario.
3. Los conceptos principales deben ser SUBCONCEPTOS reales del concepto raíz,
   NO reformulaciones ni sinónimos del mismo.
REGLA DE JERARQUÍA ESTRICTA:
- Un concepto secundario NO puede contener otros conceptos secundarios.
- Si un concepto depende de un secundario, debe ubicarse como TERCIARIO.
- No se permiten cadenas secundario → secundario.

🔒 REGLA DE PRESERVACIÓN DE PROFUNDIDAD (CRÍTICA):
- Si en la Fase 1 un concepto secundario tiene desgloses claros,
  DEBEN mantenerse como conceptos terciarios.
- NO eliminar profundidad conceptual ya existente.
- Esta fase ORGANIZA, no SIMPLIFICA.

❌ EVITAR:
- Cortar conceptos importantes por límites artificiales
- Eliminar niveles conceptuales presentes en Fase 1
- Simplificar en exceso temas complejos

✅ PRIORIZAR:
- Preservar riqueza conceptual
- Mantener coherencia semántica
- Jerarquías claras y pedagógicamente útiles

══════════════════════════════════════
FORMATO DE SALIDA OBLIGATORIO
══════════════════════════════════════

- Usa EXCLUSIVAMENTE formato de árbol con indentación:
  Raíz
  ├── Principal
  │   ├── Secundario
  │   │   └── Terciario

- NO incluyas encabezados, títulos ni explicaciones adicionales.
- NO agregues texto narrativo después del árbol.
- El árbol generado es la ÚNICA fuente válida para la Fase 2.

══════════════════════════════════════
EJEMPLO DE SALIDA VÁLIDA
══════════════════════════════════════

Tectología como Teoría del Pensamiento Sistémico
├── Tectología
│   ├── Sistemas Organizacionales
│   │   ├── Sistemas Organizados
│   │   ├── Sistemas Desorganizados
│   │   └── Sistemas Neutrales
│   ├── Principios Organizacionales Universales
│   └── Evolución y Adaptación de Sistemas
├── Pensamiento Sistémico
│   ├── Análisis Complejo
│   │   ├── Interdependencia
│   │   └── Totalidad
│   ├── Ciclos y Oscilaciones
│   └── Visión Holística
└── Cibernética
    ├── Retroalimentación en Sistemas
    ├── Control en Sistemas
    └── Dimensión Ética y Social

NO generes JSON todavía.
"""
 
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=prompt_fase15
        )

        run15 = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=assistant_id
        )

        completed_run15 = await esperar_run_completado(thread_id, run15.id)

        fase15_data = obtener_mensaje_del_run(
            client.beta.threads.messages.list(thread_id=thread_id),
            completed_run15.id
        )
        print("✅ Fase 15 completada")
        print("📄 Fase 15 output (primeros 500 chars):")
        print(fase15_data[:50000])
        print("-" * 80)
        if not fase15_data:
            return {"error": "Fase 1.5 no devolvió información"}

        print("✅ Fase 1.5 pedagógica completada")

        # -----------------------------------------------------------------------
        # 🟩 FASE 2 → GENERACIÓN DEL MAPA CONCEPTUAL EN JSON PEDAGÓGICO
        # -----------------------------------------------------------------------
        # NUEVO SISTEMA MÁS FLEXIBLE
        prompt_fase2= f"""
Fase 2 — GENERACIÓN DEL MAPA CONCEPTUAL PEDAGÓGICO (SOLO JSON)
ACLARACIÓN DE ROL:
Las reglas inteligentes SOLO aplican para:
- asignación de IDs
- orden de aparición
NO aplican para:
- creación
- eliminación
- reinterpretación
- fusión de conceptos

ROL OBLIGATORIO:
Actúas como un SERIALIZADOR ESTRUCTURAL.
NO reinterpretas, NO renombras, NO agregas conceptos.
Tu única tarea es transformar EXACTAMENTE la jerarquía dada en JSON.
REGLA DE FIDELIDAD ABSOLUTA:
- Todo concepto del JSON DEBE existir literalmente en la jerarquía Fase 1.5
- Si un concepto no aparece en la jerarquía, NO puede aparecer en el JSON

Transforma la jerarquía pedagógica en mapa conceptual JSON:

--- INICIO JERARQUÍA FASE 1.5 ---
{fase15_data}
--- FIN JERARQUÍA FASE 1.5 ---

🚀 **NUEVA JERARQUÍA FLEXIBLE - ADAPTATIVA AL CONTENIDO:**

CONSISTENCIA DE IDs (MANTENER):
• Concepto raíz: cp_1
• Conceptos principales: cp_2, cp_3, cp_4, cp_5, 
• Conceptos secundarios: cs_X_Y (X = principal, Y = índice)
• Conceptos terciarios: ct_X_Y_Z (OPCIONAL, solo si necesario)

JERARQUÍA ADAPTATIVA:
• 1 concepto raíz (cp_1)
• 3-6 conceptos principales (SEGÚN COMPLEJIDAD DEL CONTENIDO)
• 2-6 conceptos secundarios por principal (SEGÚN NECESIDAD)
• 0-3 conceptos terciarios por secundario (SOLO SI APORTA VALOR)
• TOTAL: 10-55 conceptos

🎯 **PRINCIPIOS PEDAGÓGICOS (MÁS IMPORTANTES QUE LOS LÍMITES):**
1. CLARIDAD sobre completitud
2. PROFUNDIDAD sobre amplitud
3. SIGNIFICADO sobre cantidad
4. ESTRUCTURA LÓGICA sobre reglas arbitrarias

📋 **REGLAS INTELIGENTES (NO MECÁNICAS):**
- Si el contenido tiene 6-8 conceptos principales CLAVE, inclúyelos todos
- Si un concepto principal necesita 5-6 secundarios para ser claro, inclúyelos
- Los terciarios son OPCIONALES - solo cuando desglosan conceptos complejos
- MEJOR un mapa COMPLETO que uno "recortado por reglas"

⚡ **EJEMPLO DE ESTRUCTURA VÁLIDA (como la que necesitas):**
Tectología como Teoría del Pensamiento Sistémico (raíz)
├── Tectología (principal 1)
│   ├── Sistemas Organizacionales (secundario 1.1)
│   ├── Enfoque Interdisciplinario (secundario 1.2)
│   ├── Principios Organizacionales Universales (secundario 1.3)
│   └── Evolución y Adaptación de Sistemas (secundario 1.4)
├── Pensamiento Sistémico (principal 2)
│   ├── Análisis Complejo (secundario 2.1)
│   ├── Ciclos y Oscilaciones (secundario 2.2)
│   └── Ver en Totalidades (secundario 2.3)
└── Cibernética (principal 3)
    ├── Retroalimentación en Sistemas (secundario 3.1)
    ├── Control en Sistemas (secundario 3.2)
    └── Ética y Aspectos Sociales (secundario 3.3)

ESTRUCTURA JSON (FLEXIBLE):
{{
  "titulo": "Mapa Conceptual: {titulo_guion}",
  "conceptos": [
    {{
      "id": "cp_1",
      "nombre": "[CONCEPTO RAÍZ]",
      "nivel": "raiz"
    }},
    {{
      "id": "cp_2",
      "nombre": "Concepto Principal 1", 
      "nivel": "principal",
      "padre": "cp_1"
    }},
    {{
      "id": "cs_2_1",
      "nombre": "Concepto Secundario 2.1",
      "nivel": "secundario", 
      "padre": "cp_2"
    }},
    {{
      "id": "cs_2_2",
      "nombre": "Concepto Secundario 2.2",
      "nivel": "secundario",
      "padre": "cp_2"
    }},
    // ... TANTOS CONCEPTOS COMO SEA NECESARIO
  ],
  "relaciones": [
    {{ "origen": "cp_1", "destino": "cp_2" }},
    {{ "origen": "cp_2", "destino": "cs_2_1" }},
    // ... TANTAS RELACIONES COMO SEA NECESARIO
  ]
}}

🎯 **CRITERIO FINAL DE CALIDAD:**
- ¿El mapa representa fielmente la complejidad del tema?
- ¿La estructura es pedagógicamente útil?
- ¿Los conceptos están bien organizados jerárquicamente?
- ¿Hay coherencia semántica entre niveles?
# AGREGAR ESTO AL PROMPT DE LA FASE 2
🚨 **REGLA DE NO-REDUNDANCIA ABSOLUTA:**

ANTES DE GENERAR EL JSON, VERIFICAR:
1. NINGÚN concepto puede aparecer en dos niveles diferentes
2. NINGÚN concepto puede tener nombres idénticos o casi idénticos
3. Si un concepto aparece como principal, NO puede aparecer como secundario/terciario

EJEMPLOS DE ERRORES A EVITAR:
❌ "Principios Organizativos Universales" como cp_4 Y cs_2_4
❌ "Estructura de Sistemas" como cs_4_1 Y "Estructuras de Sistemas" como ct_2_4_1
❌ Cualquier repetición conceptual entre niveles

SI DETECTAS REDUNDANCIAS:
- ELIMINA las versiones redundantes
- MANTÉN el concepto en el nivel más apropiado
- AJUSTA las relaciones en consecuencia
SI LA RESPUESTA ES SÍ, EL MAPA ES VÁLIDO aunque tenga 8 principales o 6 secundarios.
VERIFICACIÓN FINAL:
- Cada concepto secundario debe poder formularse como:
  "Tipo / aspecto / dimensión de [concepto principal]"
- Si no es posible, revisar su nivel.

ENTREGA ÚNICAMENTE EL JSON SIN NADA MÁS.
"""
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=prompt_fase2
        )

        run2 = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=assistant_id
        )

        completed_run2 = await esperar_run_completado(thread_id, run2.id)

        final_json = obtener_mensaje_del_run(
            client.beta.threads.messages.list(thread_id=thread_id),
            completed_run2.id
        )
        print("✅ Fase 2 completada")
        print("📄 Fase 2 output (primeros 500 chars):")
        print(final_json[:50000])
        print("-" * 80)
        if not final_json:
            return {"error": "Fase 2 no devolvió el JSON"}

        print("✅ Fase 2 completada → JSON pedagógico listo")
        return {"mapa_conceptual": final_json, "thread": thread_id}

    except Exception as e:
        print("❌ Error en proceso 3-fases pedagógicas:", e)
        return {"error": f"Fallo general: {str(e)}"}




def obtener_mensaje_del_run(messages, run_id):
    """
    Devuelve SOLO el mensaje generado por el run actual.
    Evita mezclar mensajes anteriores del mismo thread.
    """
    for msg in reversed(messages.data):  # recorrer del más nuevo al más antiguo
        if msg.role == "assistant" and getattr(msg, "run_id", None) == run_id:
            for c in msg.content:
                if hasattr(c, "text") and c.text:
                    return c.text.value.strip()
    return None


@router.post("/generar_flashcards/{assistant_id}")
async def generar_flashcards_api(
    assistant_id: str,
    thread_id: str = Form(...),
    vector_id: str = Form(...)
):
    
    print(f"🎴 Generando flashcards para assistant_id={assistant_id}, thread_id={thread_id}, vector_id={vector_id}")
    
    try:
        
        prompt = """Eres un asistente educativo especializado en crear flashcards de estudio.

ANALIZA el contenido educativo y genera 8-12 flashcards efectivas.

REGLAS ESTRICTAS:
- Cada flashcard debe tener UNA pregunta clara en el frente
- Cada flashcard debe tener UNA respuesta completa en el reverso
- Las preguntas deben ser sobre conceptos específicos, no generales
- Las respuestas deben ser educativas y explicativas
- Usa categorías: "concepto", "definicion", "aplicacion"

EJEMPLO DE FORMATO CORRECTO:
{
  "flashcards": [
    {
      "pregunta": "¿Qué es la fotosíntesis?",
      "respuesta": "Proceso donde las plantas convierten luz solar en energía química",
      "categoria": "definicion"
    }
  ]
}

SOLO devuelve el JSON, sin texto adicional."""

        # Enviar mensaje
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=prompt
        )

        # Crear run
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=assistant_id,
        )

        # Esperar finalización
        completed_run = await esperar_run_completado(thread_id, run.id, timeout=90)

        if not completed_run or completed_run.status != "completed":
            return {"error": "El run no completó correctamente"}

        # Obtener solo el mensaje correcto
        messages = client.beta.threads.messages.list(thread_id=thread_id)
        flashcards_data = obtener_mensaje_del_run(messages, completed_run.id)

        if not flashcards_data or len(flashcards_data.strip()) < 20:
            return {"error": "No se pudieron generar flashcards válidas."}

        return {"flashcards": flashcards_data, "thread": thread_id}

    except Exception as e:
        print("❌ Error generando flashcards:", e)
        return {"error": f"Fallo generando flashcards: {str(e)}"}


@router.post("/generar_glosario/{assistant_id}")
async def generar_glosario_api(
    assistant_id: str,
    thread_id: str = Form(...),
    vector_id: str = Form(...)
):
    
    print(f"📚 Generando glosario para assistant_id={assistant_id}, thread_id={thread_id}, vector_id={vector_id}")
    
    try:
        # ✅ PRIMERO: Verificar y esperar runs activos
        active_runs = client.beta.threads.runs.list(thread_id=thread_id)
        active_runs_list = list(active_runs)
        
        print(f"🔍 Runs activos en thread: {len(active_runs_list)}")
        
        for run in active_runs_list:
            if run.status in ["queued", "in_progress"]:
                print(f"🔄 Esperando que termine run activo: {run.id}")
                completed_run = await esperar_run_completado(thread_id, run.id)
                if not completed_run or completed_run.status != "completed":
                    print(f"❌ Run {run.id} no completó correctamente")

        # ✅ PROMPT MEJORADO (Versión de la profesora guía)
        prompt = """Eres un Asistente IA especializado en educación superior, alfabetización conceptual y diseño instruccional.

Tu tarea es leer y analizar cuidadosamente el documento cargado en el vector_store y generar un GLOSARIO altamente curado y pedagógicamente útil.

REGLA PRINCIPAL: Debes distinguir entre dos tipos de contenido:
1. CONTENIDO TEMÁTICO: Conceptos, teorías, métodos y herramientas propios de la disciplina del documento.
2. ENFOQUES PEDAGÓGICOS: Metodologías de enseñanza, evaluación, diseño instruccional (solo incluir estos si el documento específicamente trata sobre educación o pedagogía).

CRITERIOS DE SELECCIÓN DE TÉRMINOS:
- Si el documento es sobre una disciplina NO PEDAGÓGICA (ej: biología, ingeniería, literatura): selecciona SOLO conceptos de esa disciplina.
- Si el documento es SOBRE PEDAGOGÍA o ENSEÑANZA: puedes incluir tanto conceptos pedagógicos como ejemplos de otras disciplinas que se usen como casos de estudio.
- Prioriza términos que sean: fundamentales para entender la materia, recurrentes en el documento, técnicamente precisos y con aplicación práctica.

FORMATO DE DEFINICIONES:
- Claridad y precisión conceptual.
- Contextualización en la disciplina.
- Ejemplo práctico extraído o inspirado en el documento.
- Lenguaje accesible para estudiantes universitarios.

CATEGORÍAS PERMITIDAS (asigna la más precisa):
- "concepto": Ideas, nociones, definiciones básicas.
- "tecnico": Términos especializados de la disciplina.
- "proceso": Secuencias, métodos, procedimientos.
- "principio": Leyes, normas, fundamentos teóricos.
- "marco_teorico": Teorías, modelos, enfoques conceptuales.
- "herramienta": Instrumentos, técnicas, recursos.

RESTRICCIONES IMPORTANTES:
- NO mezcles conceptos de diferentes disciplinas a menos que el documento lo haga explícitamente.
- NO inventes términos que no aparezcan o no estén claramente implícitos.
- NO incluyas metodologías de enseñanza (como "aprendizaje colaborativo") a menos que el documento trate específicamente sobre pedagogía.
- Mantén entre 8 y 15 términos, priorizando calidad sobre cantidad.

FORMATO DE RESPUESTA OBLIGATORIO — Devuelve SOLO JSON válido:

{
    "glosario": [
        {
            "termino": "Nombre del término",
            "definicion": "Definición clara, precisa y técnicamente correcta.",
            "categoria": "concepto | tecnico | proceso | principio | marco_teorico | herramienta",
            "ejemplo": "Caso práctico o ejemplo de aplicación (opcional pero recomendable)"
        }
    ]
}

No incluyas explicaciones, texto adicional ni markdown. Devuelve solo el JSON."""
        # Enviar el prompt dentro del mismo thread
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=prompt
        )

        # Crear run
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=assistant_id,
        )
        
        # ✅ USAR FUNCIÓN ESPERAR_RUN_COMPLETADO
        completed_run = await esperar_run_completado(thread_id, run.id, timeout=90)

        if not completed_run or completed_run.status != "completed":
            return {"error": "El run no completó correctamente"}

        # Obtener mensajes del hilo
        messages = client.beta.threads.messages.list(thread_id=thread_id)
        
        # ✅ USAR OBTENER_MENSAJE_DEL_RUN EN LUGAR DE INTERPRETAR_MENSAJES_ASSISTANT
        glosario_data = obtener_mensaje_del_run(messages, completed_run.id)

        if not glosario_data or len(glosario_data.strip()) < 20:
            print("⚠️ No se obtuvo glosario válido del modelo.")
            return {"error": "No se pudo generar un glosario válido."}

        print("✅ Glosario generado correctamente.")
        return {"glosario": glosario_data, "thread": thread_id}

    except Exception as e:
        print("❌ Error generando glosario:", e)
        return {"error": f"Fallo generando glosario: {str(e)}"}



class NanoBananaService:
    def __init__(self):
        self.api_key = "2f4e478918ba2cc014f759551102d5b2"
        self.base_url = "https://api.nanobananaapi.ai/api/v1/nanobanana"
        self.semaphore = asyncio.Semaphore(2)  # ✅ Limitar a 2 requests simultáneas
        print("🔑 LLAVE:", self.api_key)

    async def generate_infography(self, prompt: str) -> Optional[Dict[str, str]]:
        """Genera imagen con Nano Banana PRO - Retorna dict con url y base64"""
        async with self.semaphore:
            try:
                print(f"🔄 Iniciando generación PRO")
                
                # 1. Crear la tarea con timeout controlado
                task_id = await asyncio.wait_for(
                    self._create_pro_task(prompt), 
                    timeout=30.0
                )
                
                if not task_id:
                    print("❌ No se pudo crear tarea PRO")
                    return None

                print(f"✅ Tarea PRO creada - Task ID: {task_id}")

                # 2. Esperar con polling más eficiente
                image_url = await asyncio.wait_for(
                    self._wait_for_task_completion_optimized(task_id),
                    timeout=240.0  # 4 minutos máximo
                )
                
                if not image_url:
                    print("❌ No se pudo obtener URL de imagen")
                    return None

                # 3. Descargar imagen
                image_base64 = await asyncio.wait_for(
                    self._download_image(image_url),
                    timeout=60.0  # 1 minuto para descarga
                )
                
                if not image_base64:
                    print("❌ No se pudo descargar imagen")
                    return None
                
                # 4. Retornar ambos valores
                return {
                    "url": image_url,
                    "base64": image_base64
                }

            except asyncio.TimeoutError:
                print("⏰ Timeout en generación completa de imagen PRO")
                return None
            except Exception as e:
                print(f"❌ Error Nano Banana PRO: {e}")
                return None   
    async def _create_pro_task(self, prompt: str) -> Optional[str]:
        """Versión optimizada para crear tarea"""
        url = f"{self.base_url}/generate-pro"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        # ✅ Prompt más corto para ser más rápido
        prompt_optimizado = prompt[:500] if len(prompt) > 500 else prompt
        
        payload = {
            "prompt": prompt_optimizado,
            "imageUrls": [""],
            "resolution": "1K",  # ✅ Reducir a 1K para ser más rápido
            "aspectRatio": "16:9",
            "callBackUrl": ""
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get("code") == 200:
                        return data.get("data", {}).get("taskId")
                    else:
                        print(f"❌ API PRO error: {data.get('message', 'Unknown')}")
                        return None
                else:
                    print(f"❌ HTTP Error: {response.status_code}")
                    return None
                    
        except Exception as e:
            print(f"❌ Error creando tarea: {e}")
            return None

    async def _wait_for_task_completion_optimized(self, task_id: str) -> Optional[str]:
        """Polling optimizado con intervalos variables"""
        print(f"⏳ Esperando imagen PRO (Task: {task_id})...")
        
        url = f"{self.base_url}/record-info"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        
        start_time = time.time()
        attempts = 0
        
        while time.time() - start_time < 240:  # 4 minutos máximo
            attempts += 1
            elapsed = int(time.time() - start_time)
            
            # ✅ Intervalo variable: más frecuente al inicio, menos al final
            if elapsed < 30:
                interval = 3  # Cada 3 segundos primeros 30s
            elif elapsed < 120:
                interval = 5  # Cada 5 segundos siguiente minuto
            else:
                interval = 8  # Cada 8 segundos después
            
            try:
                params = {"taskId": task_id}
                
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(url, headers=headers, params=params)
                    
                    if response.status_code == 200:
                        data = response.json()
                        success_flag = data.get("data", {}).get("successFlag")
                        
                        if success_flag == 1:  # ✅ Éxito
                            image_url = data.get("data", {}).get("response", {}).get("resultImageUrl")
                            if image_url:
                                total_time = int(time.time() - start_time)
                                print(f"✅ Imagen lista en {total_time}s")
                                return image_url
                            
                        elif success_flag in [2, 3]:  # ❌ Falló
                            print(f"❌ Tarea falló después de {elapsed}s")
                            return None
                        
                        # ✅ Si lleva más de 30 segundos y no ha progresado, dar update
                        if elapsed > 30 and attempts % 5 == 0:
                            print(f"⏳ Procesando... ({elapsed}s)")
                            
                    else:
                        if attempts % 3 == 0:  # No loggear cada error
                            print(f"⚠️ Error consultando estado: {response.status_code}")
                        
            except Exception as e:
                if attempts % 3 == 0:  # No loggear cada excepción
                    print(f"⚠️ Error en polling: {e}")
            
            # Esperar antes del próximo intento
            await asyncio.sleep(interval)
        
        print(f"❌ Timeout después de 240s")
        return None
    async def _download_image(self, image_url: str) -> Optional[str]:
        """Descarga imagen simple y robusta"""
        try:
            print(f"📥 Descargando imagen: {image_url}")
            
            # Usar timeout más corto pero robusto
            async with httpx.AsyncClient(timeout=90.0) as client:
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
        
nano_banana_service = NanoBananaService()
@router.post("/generar_infografia/{assistant_id}")
async def generar_infografia_api(
    assistant_id: str,
    thread_id: str = Form(...),
    vector_id: str = Form(...),
    titulo: str = Form(...),
    recursos_aprendizaje: str = Form(...),
    contenidos: str = Form(...)
):
    print(f"🎨 Generando infografía para: {titulo}")
    
    # ✅ 1. Crear prompt OPTIMIZADO para ser más rápido
    prompt = f"""Crea un prompt para DALL-E/Stable Diffusion para una infografía educativa sobre: '{titulo}'.

INSTRUCCIONES BREVES:
1. Tema principal: {titulo}
2. Conceptos clave (3-4 máximo): Extrae de: {contenidos}
3. Estilo: Infografía educativa moderna, minimalista
4. Colores: Paleta profesional universitaria
5. Incluir: Título, 3-4 secciones, iconos simples

RESPONDER SOLO con el prompt, nada más. Máximo 150 palabras."""

    try:
        # ✅ 2. Usar timeout más corto para la parte de GPT
        gpt_timeout = 60  # 60 segundos máximo para GPT
        
        # Crear task para GPT con timeout
        gpt_task = asyncio.create_task(
            procesar_con_gpt(thread_id, assistant_id, prompt)
        )
        
        try:
            prompt_para_imagen = await asyncio.wait_for(gpt_task, timeout=gpt_timeout)
        except asyncio.TimeoutError:
            print("⚠️ Timeout en GPT, usando prompt simplificado")
            prompt_para_imagen = f"Infografía educativa profesional sobre: {titulo}. Diseño moderno minimalista con 3-4 secciones, paleta de colores universitarios, iconos simples y texto claro. En español"

        print(f"✅ Prompt listo ({len(prompt_para_imagen)} chars)")

        # ✅ 3. Generar imagen con timeout específico
        imagen_timeout = 280  # 280 segundos para la imagen
        
        # Usar la instancia de nano_banana_service
        imagen_task = asyncio.create_task(
            nano_banana_service.generate_infography(prompt_para_imagen)
        )
        
        try:
            # Ahora recibe un diccionario, no solo base64
            resultado_imagen = await asyncio.wait_for(imagen_task, timeout=imagen_timeout)
        except asyncio.TimeoutError:
            print("⚠️ Timeout en generación de imagen")
            return {"error": "La generación de imagen tardó demasiado"}
        
        if resultado_imagen and resultado_imagen.get("base64"):
            return {
                "imagen_base64": resultado_imagen["base64"],
                "imagen_url": resultado_imagen["url"],  # ← NUEVO: URL de Banana
                "titulo": titulo,
                "status": "success",
                "fuente": "nano_banana_pro",
                "tiempo_estimado": "ok"
            }
        else:
            return {"error": "No se pudo generar la imagen"}

    except Exception as e:
        print("❌ Error generando infografía:", e)
        return {"error": f"Fallo generando infografía: {str(e)}"}


async def procesar_con_gpt(thread_id: str, assistant_id: str, prompt: str) -> str:
    """Procesa con GPT con manejo de errores"""
    try:
        # Crear mensaje
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=prompt
        )

        # Ejecutar run
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=assistant_id,
        )
        
        completed_run = await esperar_run_completado(thread_id, run.id, timeout=45)

        if not completed_run or completed_run.status != "completed":
            raise Exception("Run no completó correctamente")

        # Obtener respuesta
        messages = client.beta.threads.messages.list(thread_id=thread_id)
        respuesta = obtener_mensaje_del_run(messages, completed_run.id)

        if not respuesta or len(respuesta.strip()) < 10:
            raise Exception("No se pudo obtener respuesta válida")

        return respuesta.strip()
        
    except Exception as e:
        print(f"⚠️ Error en GPT: {e}")
        # Fallback a prompt simple
        return f"Infografía educativa profesional sobre el tema proporcionado. Diseño moderno, minimalista, con iconos y texto claro para estudiantes universitarios."