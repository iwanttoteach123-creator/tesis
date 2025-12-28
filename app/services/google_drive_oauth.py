# D:\Orlando\Documentos\TESIS AHORA SI\proyecto tesis claudio\AI-Want-2-Teach - copia\FERIA\backend\app\services\google_drive_oauth.py
import os
import pickle
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io
from datetime import datetime
import logging
# D:\Orlando\Documentos\TESIS AHORA SI\proyecto tesis claudio\AI-Want-2-Teach - copia\FERIA\backend\app\services\google_drive_oauth.py
import os
import pickle
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io
from datetime import datetime
import logging
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GoogleDriveOAuth:
    def __init__(self):
        self.SCOPES = ['https://www.googleapis.com/auth/drive.file']
        self.FOLDER_ID = '1x8-lvAwcqHUnpkF1p0pc_t6LF9LVP75a'
        self.service = None
        self.authenticate()
    def authenticate(self):
        try:
            creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
            if not creds_json:
                raise RuntimeError("GOOGLE_CREDENTIALS_JSON no definido")

            creds_info = json.loads(creds_json)

            flow = Flow.from_client_config(
                creds_info,
                scopes=self.SCOPES,
                redirect_uri=creds_info["installed"]["redirect_uris"][0]
            )

            auth_url, _ = flow.authorization_url(
                prompt='consent',
                access_type='offline'
            )

            logger.info("🔐 Autenticación Google Drive requerida")
            print("\nAbre este enlace y autoriza:\n")
            print(auth_url)

            code = input("\n📋 Código de autorización: ").strip()

            flow.fetch_token(code=code)
            creds = flow.credentials

            self.service = build('drive', 'v3', credentials=creds)
            logger.info("✅ Servicio de Google Drive creado")

        except Exception as e:
            logger.error(f"❌ Error en autenticación: {e}")
            raise


    
    
    def upload_file(self, file_content: bytes, filename: str = None):
        """Sube un archivo a Google Drive"""
        try:
            if not self.service:
                raise Exception("Servicio de Drive no inicializado")
            
            # Crear nombre único si no se proporciona
            if not filename:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"material_estudio_{timestamp}.zip"
            
            logger.info(f"📤 Subiendo archivo: {filename}")
            
            # Metadata del archivo
            file_metadata = {
                'name': filename,
                'parents': [self.FOLDER_ID],  # Tu carpeta
                'description': 'Material de estudio generado por AI-Want-2-Teach'
            }
            
            # Crear objeto media
            file_stream = io.BytesIO(file_content)
            media = MediaIoBaseUpload(
                file_stream,
                mimetype='application/zip',
                resumable=True
            )
            
            # Subir archivo
            request = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, name, webViewLink, size'
            )
            
            file = request.execute()
            logger.info(f"✅ Archivo subido: {file['name']} (ID: {file['id']})")
            
            # Hacer el archivo público (cualquiera con el link puede ver)
            logger.info("🔓 Configurando permisos públicos...")
            self.service.permissions().create(
                fileId=file['id'],
                body={
                    'type': 'anyone',
                    'role': 'reader'  # Solo lectura
                }
            ).execute()
            
            # Obtener información del archivo
            file_size = int(file.get('size', 0))
            size_mb = file_size / (1024 * 1024)
            
            logger.info(f"📊 Tamaño: {size_mb:.2f} MB")
            logger.info(f"🔗 Enlace: {file['webViewLink']}")
            
            return {
                'success': True,
                'message': 'Archivo subido exitosamente a Google Drive',
                'drive_link': file['webViewLink'],
                'file_id': file['id'],
                'file_name': file['name'],
                'file_size_mb': size_mb
            }
            
        except Exception as e:
            logger.error(f"❌ Error subiendo archivo: {e}")
            return {
                'success': False,
                'error': str(e)
            }
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GoogleDriveOAuth:
    def __init__(self):
        self.SCOPES = ['https://www.googleapis.com/auth/drive.file']
        self.FOLDER_ID = '1x8-lvAwcqHUnpkF1p0pc_t6LF9LVP75a'
        self.service = None
        self.authenticate()
    
    def authenticate(self):
        try:
            creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
            if not creds_json:
                raise RuntimeError("GOOGLE_CREDENTIALS_JSON no definido")

            creds_info = json.loads(creds_json)

            flow = Flow.from_client_config(
                creds_info,
                scopes=self.SCOPES,
                redirect_uri=creds_info["installed"]["redirect_uris"][0]
            )

            auth_url, _ = flow.authorization_url(
                prompt='consent',
                access_type='offline'
            )

            logger.info("🔐 Autenticación Google Drive requerida")
            print("\nAbre este enlace y autoriza:\n")
            print(auth_url)

            code = input("\n📋 Código de autorización: ").strip()

            flow.fetch_token(code=code)
            creds = flow.credentials

            self.service = build('drive', 'v3', credentials=creds)
            logger.info("✅ Servicio de Google Drive creado")

        except Exception as e:
            logger.error(f"❌ Error en autenticación: {e}")
            raise

    
    
    def upload_file(self, file_content: bytes, filename: str = None):
        """Sube un archivo a Google Drive"""
        try:
            if not self.service:
                raise Exception("Servicio de Drive no inicializado")
            
            # Crear nombre único si no se proporciona
            if not filename:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"material_estudio_{timestamp}.zip"
            
            logger.info(f"📤 Subiendo archivo: {filename}")
            
            # Metadata del archivo
            file_metadata = {
                'name': filename,
                'parents': [self.FOLDER_ID],  # Tu carpeta
                'description': 'Material de estudio generado por AI-Want-2-Teach'
            }
            
            # Crear objeto media
            file_stream = io.BytesIO(file_content)
            media = MediaIoBaseUpload(
                file_stream,
                mimetype='application/zip',
                resumable=True
            )
            
            # Subir archivo
            request = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, name, webViewLink, size'
            )
            
            file = request.execute()
            logger.info(f"✅ Archivo subido: {file['name']} (ID: {file['id']})")
            
            # Hacer el archivo público (cualquiera con el link puede ver)
            logger.info("🔓 Configurando permisos públicos...")
            self.service.permissions().create(
                fileId=file['id'],
                body={
                    'type': 'anyone',
                    'role': 'reader'  # Solo lectura
                }
            ).execute()
            
            # Obtener información del archivo
            file_size = int(file.get('size', 0))
            size_mb = file_size / (1024 * 1024)
            
            logger.info(f"📊 Tamaño: {size_mb:.2f} MB")
            logger.info(f"🔗 Enlace: {file['webViewLink']}")
            
            return {
                'success': True,
                'message': 'Archivo subido exitosamente a Google Drive',
                'drive_link': file['webViewLink'],
                'file_id': file['id'],
                'file_name': file['name'],
                'file_size_mb': size_mb
            }
            
        except Exception as e:
            logger.error(f"❌ Error subiendo archivo: {e}")
            return {
                'success': False,
                'error': str(e)
            }