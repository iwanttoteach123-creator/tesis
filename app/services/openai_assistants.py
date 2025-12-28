# services/openai_assistants.py
from typing import Optional
from fastapi import HTTPException
from api import client, instrucciones, modelo

async def create_assistant_fn(name: str) -> str:
    assistant = client.beta.assistants.create(
        name=name,
        instructions=instrucciones,
        model=modelo,
        tools=[{"type": "code_interpreter"}, {"type": "file_search"}],
    )
    return assistant.id


async def create_assistant_fn(name: str) -> str:
    assistant = client.beta.assistants.create(
        name=name,
        instructions=instrucciones,
        model=modelo,
        tools=[{"type": "code_interpreter"}, {"type": "file_search"}],
    )
    return assistant.id

async def create_vector_fn(assistant_id: str) -> str:
    # si tu endpoint create-vector recibe assistant_id, aquí lo igualas
    vector_store = client.beta.vector_stores.create(name=f"vs_{assistant_id}")
    return vector_store.id

async def delete_assistant_fn(assistant_id: str) -> None:
    client.beta.assistants.delete(assistant_id)

async def delete_vector_fn(vector_id: str) -> None:
    client.beta.vector_stores.delete(vector_id)


# app/services/openai_assistants.py
from api import crear_guion as crear_guion_endpoint  # si existe con ese nombre

async def crear_guion_fn(
    assistant_id: str,
    titulo: str,
    resultado_aprendizaje: str,
    contenido_tematico: str,
    tipo_clase: str,
    duracion: int,
    semana: int,
    vector_id: str,
) -> dict:
    """
    Reemplazo interno de POST {url_api}/crear_guion/{assistant_id}
    Retorna el mismo dict que devolvía response.json()
    """
    # si tu crear_guion en api.py es sync, quita await
    return await crear_guion_endpoint(
        assistant_id=assistant_id,
        titulo=titulo,
        resultado_aprendizaje=resultado_aprendizaje,
        contenido_tematico=contenido_tematico,
        tipo_clase=tipo_clase,
        duracion=duracion,
        semana=semana,
        vector_id=vector_id,
    )


# app/services/openai_assistants.py
import asyncio
from api import generar_resumen_api  # endpoint interno

async def generar_resumen_fn(assistant_id: str, thread_id: str, vector_id: str) -> dict:
    """
    Reemplazo interno de POST /generar_resumen/{assistant_id}
    Retorna el dict: {"resumen": ..., "thread": ...} o {"error": ...}
    """
    last_err = None
    for _ in range(3):  # mismo "max_intentos=3"
        try:
            return await generar_resumen_api(
                assistant_id=assistant_id,
                thread_id=thread_id,
                vector_id=vector_id,
            )
        except Exception as e:
            last_err = e
            await asyncio.sleep(1)
    raise Exception(f"Fallo generando resumen tras reintentos: {last_err}")
import asyncio
from api import generar_mapa_conceptual_api  # el endpoint interno :contentReference[oaicite:2]{index=2}

async def generar_mapa_conceptual_fn(assistant_id: str, thread_id: str, vector_id: str, titulo_guion: str) -> dict:
    """
    Reemplazo interno de POST /generar_mapa_conceptual/{assistant_id}
    Retorna dict: {"mapa_conceptual": "...json...", "thread": thread_id} :contentReference[oaicite:3]{index=3}
    """
    last_err = None
    for _ in range(3):  # mismo max_intentos=3
        try:
            return await generar_mapa_conceptual_api(
                assistant_id=assistant_id,
                thread_id=thread_id,
                vector_id=vector_id,
                titulo_guion=titulo_guion,
            )
        except Exception as e:
            last_err = e
            await asyncio.sleep(1)
    raise Exception(f"Fallo generando mapa conceptual tras reintentos: {last_err}")


# app/services/openai_assistants.py
import asyncio
from api import generar_flashcards_api

async def generar_flashcards_fn(assistant_id: str, thread_id: str, vector_id: str) -> dict:
    """
    Reemplazo interno de POST /generar_flashcards/{assistant_id}
    Retorna dict con key "flashcards".
    """
    return await generar_flashcards_api(
        assistant_id=assistant_id,
        thread_id=thread_id,
        vector_id=vector_id,
    )
# app/services/openai_assistants.py
from api import generar_glosario_api

async def generar_glosario_fn(assistant_id: str, thread_id: str, vector_id: str) -> dict:
    """
    Reemplazo interno de POST /generar_glosario/{assistant_id}
    """
    return await generar_glosario_api(
        assistant_id=assistant_id,
        thread_id=thread_id,
        vector_id=vector_id,
    )
# app/services/openai_assistants.py
from api import generar_infografia_api

async def generar_infografia_fn(
    assistant_id: str,
    thread_id: str,
    vector_id: str,
    recursos_aprendizaje,
    contenidos,
    titulo: str,
) -> dict:
    """
    Reemplazo interno de POST /generar_infografia/{assistant_id}
    """
    return await generar_infografia_api(
        assistant_id=assistant_id,
        thread_id=thread_id,
        vector_id=vector_id,
        recursos_aprendizaje=recursos_aprendizaje,
        contenidos=contenidos,
        titulo=titulo,
    )

