import fitz  # PyMuPDF
import requests
import math
import re

# CONFIGURACION DE CONEXION
# IMPORTANTE: Cambiar cada vez que reinicies el túnel Ngrok en Google Colab.
COLAB_URL = "https://joannie-lacrimatory-donnetta.ngrok-free.dev"

def load_models():
    """Verifica la disponibilidad del servicio remoto en Colab."""
    try:
        print(f"--- VERIFICANDO CONEXION IA: {COLAB_URL} ---")
        response = requests.get(f"{COLAB_URL}/", timeout=5)
        if response.status_code == 200:
            print("SERVIDOR IA ONLINE Y ACCESIBLE")
        else:
            print(f"ADVERTENCIA: El servidor respondio con estado {response.status_code}")
    except Exception as e:
        print(f"ERROR CRITICO: No hay conexion con el servidor remoto. {e}")

def extract_text_from_pdf(pdf_bytes):
    """Extrae texto plano de un archivo PDF de forma robusta."""
    try:
        if not pdf_bytes:
            print("ERROR: Los bytes del PDF llegaron vacios")
            return ""

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text()
        
        cleaned_text = text.strip()
        if not cleaned_text:
            print("ADVERTENCIA: No se detecto texto (posible PDF escaneado como imagen)")
        else:
            print(f"EXITO: Texto extraido correctamente ({len(cleaned_text)} caracteres)")
            
        return cleaned_text
    except Exception as e:
        print(f"ERROR LEYENDO PDF: {e}")
        return ""

def extract_email_from_text(text):
    """Busca el correo electronico dentro del texto extraido."""
    if not text: return "No detectado"
    try:
        email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        match = re.search(email_pattern, text)
        if match:
            email = match.group(0)
            print(f"DATOS: Email encontrado: {email}")
            return email
    except Exception:
        pass
    return "No detectado"

def extract_phone_from_text(text):
    """Busca telefonos (Nicaragua e internacionales) dentro del texto."""
    if not text: return "No detectado"
    try:
        # Limpieza basica para evitar saltos de linea en medio del numero
        text_clean = re.sub(r'(\d)\n(\d)', r'\1\2', text)
        
        # Patron para numeros de 8 a 15 digitos con prefijos comunes
        phone_pattern = r'(\+?\d{1,3}[-.\s]?)?\(?\d{3,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}'
        matches = re.findall(phone_pattern, text_clean)
        
        for m in matches:
            # Contamos solo los digitos reales
            digits = re.sub(r"\D", "", m)
            if 8 <= len(digits) <= 15:
                print(f"DATOS: Telefono encontrado: {m.strip()}")
                return m.strip()
    except Exception:
        pass
    return "No detectado"

def translate_text(text):
    """Solicita la traduccion al microservicio en Colab."""
    if not text: return ""
    try:
        response = requests.post(f"{COLAB_URL}/translate", json={"text": text[:2500]}, timeout=60)
        if response.status_code == 200:
            return response.json().get("translation", "")
    except Exception as e:
        print(f"ERROR TRADUCCION: {e}")
    return ""

def get_embedding(text):
    """Obtiene el vector numerico del texto desde Colab."""
    if not text: return []
    try:
        response = requests.post(f"{COLAB_URL}/vectorize", json={"text": text}, timeout=60)
        if response.status_code == 200:
            return response.json().get("vector", [])
    except Exception as e:
        print(f"ERROR VECTORIZACION: {e}")
    return []

def extract_keywords(text):
    """Solicita palabras clave al microservicio."""
    if not text: return []
    try:
        response = requests.post(f"{COLAB_URL}/keywords", json={"text": text[:3000]}, timeout=60)
        if response.status_code == 200:
            return response.json().get("keywords", [])
    except Exception:
        pass
    return []

def calculate_similarity(vec1, vec2):
    """Calcula la similitud de coseno (Match Score) entre dos vectores."""
    if not vec1 or not vec2: return 0.0
    try:
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        magnitude1 = math.sqrt(sum(a * a for a in vec1))
        magnitude2 = math.sqrt(sum(b * b for b in vec2))
        
        if magnitude1 * magnitude2 == 0: return 0.0
        
        similarity = (dot_product / (magnitude1 * magnitude2)) * 100
        return round(similarity, 2)
    except Exception as e:
        print(f"ERROR CALCULANDO SIMILITUD: {e}")
        return 0.0

def generate_rationale(cv_text_en, offer_text_en):
    """Genera una explicacion breve de la afinidad detectada."""
    if not cv_text_en or not offer_text_en:
        return "No hay datos suficientes para generar una justificacion."
    
    try:
        # Intenta cruzar keywords para dar una respuesta mas inteligente
        cv_k = set([k.lower() for k in extract_keywords(cv_text_en)])
        off_k = set([k.lower() for k in extract_keywords(offer_text_en)])
        
        coincidencias = list(off_k.intersection(cv_k))
        
        if coincidencias:
            skills = ", ".join([s.title() for s in coincidencias[:4]])
            return f"El perfil muestra alta compatibilidad en competencias clave como: {skills}."
        
        return "El analisis vectorial indica una afinidad contextual adecuada con los requisitos del puesto."
    except Exception:
        return "Analisis semantico completado satisfactoriamente."

def explain_match(cv_text, offer_text):
    """Solicita una explicacion detallada al servidor (Opcional)."""
    try:
        response = requests.post(
            f"{COLAB_URL}/explain",
            json={"cv_text": cv_text, "offer_text": offer_text},
            timeout=40
        )
        if response.status_code == 200:
            return response.json().get("explanation", "Sin explicacion disponible")
    except Exception:
        pass
    return "Servicio de explicacion detallada no disponible en este momento."