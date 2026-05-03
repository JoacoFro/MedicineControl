import google.generativeai as genai
from config import GEMINI_API_KEY

genai.configure(api_key=GEMINI_API_KEY)

print("🔍 Listando modelos disponibles para tu API Key...")
try:
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(f"✅ Modelo encontrado: {m.name}")
except Exception as e:
    print(f"❌ No se pudo listar: {e}")