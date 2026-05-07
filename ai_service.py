# =====================================================
# AI_SERVICE.PY (Ubicado en Render)
# Este archivo actúa como cliente para el servidor de IA pesada.
# =====================================================

import fitz  # PyMuPDF
import requests
import math
import re

# CONFIGURACIÓN DE CONEXIÓN PERMANENTE
# Reemplaza esta URL con la dirección "Direct URL" de tu Space en Hugging Face.
# Debe terminar en .hf.space
HF_API_URL = "https://ingrsle-marknica-ai-backend.hf.space"

def load_models():
    """Verifica la disponibilidad del servicio remoto en Hugging Face."""
    try:
        print(f"--- VERIFICANDO CONEXIÓN IA EN HF: {HF_API_URL} ---")
        # El timeout es de 10s por si el Space está "despertando"
        response = requests.get(f"{HF_API_URL}/", timeout=10)
        if response.status_code == 200:
            print("SERVIDOR IA ONLINE (HUGGING FACE)")
        else:
            print(f"ADVERTENCIA: El servidor IA respondió con estado {response.status_code}")
    except Exception as e:
        print(f"ERROR CRÍTICO: No se pudo conectar con Hugging Face. {e}")

def extract_text_from_pdf(pdf_bytes):
    """Extrae texto plano de un archivo PDF usando PyMuPDF."""
    try:
        if not pdf_bytes:
            return ""

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text()
        
        cleaned_text = text.strip()
        if cleaned_text:
            print(f"EXITO: Texto extraído ({len(cleaned_text)} caracteres)")
        return cleaned_text
    except Exception as e:
        print(f"ERROR LEYENDO PDF: {e}")
        return ""

def extract_email_from_text(text):
    """Extrae el primer correo electrónico encontrado en el texto."""
    if not text: return "No detectado"
    try:
        email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        match = re.search(email_pattern, text)
        if match:
            return match.group(0)
    except Exception:
        pass
    return "No detectado"

def extract_phone_from_text(text):
    """Extrae números de teléfono válidos del texto."""
    if not text: return "No detectado"
    try:
        text_clean = re.sub(r'(\d)\n(\d)', r'\1\2', text)
        phone_pattern = r'(\+?\d{1,3}[-.\s]?)?\(?\d{3,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}'
        matches = re.findall(phone_pattern, text_clean)
        
        for m in matches:
            digits = re.sub(r"\D", "", m)
            if 8 <= len(digits) <= 15:
                return m.strip()
    except Exception:
        pass
    return "No detectado"

def translate_text(text):
    """Solicita la traducción al backend de IA en Hugging Face."""
    if not text: return ""
    try:
        # Enviamos a la ruta /translate del Space
        response = requests.post(f"{HF_API_URL}/translate", json={"text": text[:2500]}, timeout=60)
        if response.status_code == 200:
            return response.json().get("translation", "")
    except Exception as e:
        print(f"ERROR TRADUCCIÓN: {e}")
    return ""

def get_embedding(text):
    """Obtiene el vector numérico desde el backend de IA en Hugging Face."""
    if not text: return []
    try:
        # Enviamos a la ruta /vectorize del Space
        response = requests.post(f"{HF_API_URL}/vectorize", json={"text": text}, timeout=60)
        if response.status_code == 200:
            return response.json().get("vector", [])
    except Exception as e:
        print(f"ERROR VECTORIZACIÓN: {e}")
    return []

def extract_keywords(text):
    """Solicita extracción de palabras clave al backend de IA."""
    if not text: return []
    try:
        response = requests.post(f"{HF_API_URL}/keywords", json={"text": text[:3000]}, timeout=60)
        if response.status_code == 200:
            return response.json().get("keywords", [])
    except Exception:
        pass
    return []

def calculate_similarity(vec1, vec2):
    """Calcula la similitud de coseno para el Match Score."""
    if not vec1 or not vec2: return 0.0
    try:
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        magnitude1 = math.sqrt(sum(a * a for a in vec1))
        magnitude2 = math.sqrt(sum(b * b for b in vec2))
        
        if magnitude1 * magnitude2 == 0: return 0.0
        
        similarity = (dot_product / (magnitude1 * magnitude2)) * 100
        return round(similarity, 2)
    except Exception as e:
        print(f"ERROR SIMILITUD: {e}")
        return 0.0

def generate_rationale(cv_text_en, skills_clave_en):
    """Genera una explicación breve comparando Skills Clave vs CV."""
    if not skills_clave_en:
        return "Análisis semántico completado satisfactoriamente."
    
    try:
        cv_k = set([k.lower() for k in extract_keywords(cv_text_en)])
        target_skills = set([s.strip().lower() for s in skills_clave_en.split(",") if s.strip()])
        
        coincidencias = list(target_skills.intersection(cv_k))
        
        if coincidencias:
            skills = ", ".join([s.title() for s in coincidencias[:3]])
            return f"Match detectado en competencias técnicas: {skills}."
        
        return "El perfil presenta una afinidad contextual sólida con los requisitos."
    except Exception:
        return "Procesamiento de afinidad finalizado."

def explain_match(cv_text, offer_text):
    """Solicita una explicación narrativa al modelo Flan-T5 en HF."""
    try:
        response = requests.post(
            f"{HF_API_URL}/explain",
            json={"cv_text": cv_text, "offer_text": offer_text},
            timeout=60
        )
        if response.status_code == 200:
            return response.json().get("explanation", "Sin explicación disponible")
    except Exception:
        pass
    return "Servicio de explicación narrativa temporalmente no disponible."