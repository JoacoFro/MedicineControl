import os
import sys
import django
import asyncio
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
import google.generativeai as genai
from asgiref.sync import sync_to_async
# IMPORTANTE: Necesitamos esto para limpiar conexiones muertas
from django.db import connection
from django.utils import timezone

# --- 1. PUENTE CON DJANGO ---
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

load_dotenv(BASE_DIR / ".env")
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from medicine_control.models import Insumo, Pedido, Salida, Envio

# --- 2. WRAPPERS PARA DJANGO ---

@sync_to_async
def obtener_insumos_db():
    """Puente asíncrono que limpia la conexión SSL antes de consultar."""
    connection.close_if_unusable_or_obsolete()
    return list(Insumo.objects.all())

# --- 3. FUNCIONES DE LÓGICA (TOOLS CORREGIDAS) ---

def consultar_estado_stock():
    """Consulta el stock detallado limpiando la conexión SSL."""
    try:
        connection.close_if_unusable_or_obsolete()
        insumos = Insumo.objects.all()
        if not insumos:
            return "No hay insumos registrados."
        
        reporte = "📊 Estado Actual:\n"
        for i in insumos:
            aut = i.autonomia_smart
            emoji = "🔴" if aut <= 10 else "🟡" if aut <= 15 else "🟢"
            reporte += (f"- {i.nombre}: {i.total_unidades_reales} un. "
                        f"({i.stock_actual_cajas} cajas, {i.backup_unidades} backup). "
                        f"Autonomía: {emoji} {aut} días.\n")
        return reporte
    except Exception as e:
        return f"Error al consultar stock: {e}"

def registrar_movimiento(accion: str, cantidad: int, tipo_stock: str, nombre_insumo: str = "Sonda"):
    try:
        connection.close_if_unusable_or_obsolete()
        
        # Limpieza de plurales
        nombre_busqueda = nombre_insumo.rstrip('sS') 
        insumo = Insumo.objects.filter(nombre__icontains=nombre_busqueda).first()
        
        if not insumo:
            return f"❌ ERROR: No encontré el insumo '{nombre_insumo}'."

        if accion == "descargar":
            # Normalizamos lo que pueda mandar la IA para que entre acá
            if tipo_stock in ["stock_normal", "cajas", "principal", "normal"]:
                insumo.stock_actual_cajas -= cantidad
                Salida.objects.create(
                    insumo=insumo, 
                    cantidad_cajas=cantidad, 
                    cantidad=cantidad * 30, 
                    tipo_stock='stock_normal' # Mismo valor que Django Choices
                )
                tipo_usado = "Stock Normal (Cajas)"
            else:
                insumo.backup_unidades -= cantidad
                Salida.objects.create(
                    insumo=insumo, 
                    cantidad_cajas=0, 
                    cantidad=cantidad, 
                    tipo_stock='seguridad' # Mismo valor que Django Choices
                )
                tipo_usado = "Stock de Seguridad (Unidades)"

        insumo.save()
        insumo.refresh_from_db()
        
        return f"✅ Descarga exitosa en {insumo.nombre} ({tipo_usado}). Nuevo total: {insumo.total_unidades_reales} un."

    except Exception as e:
        return f"❌ Error técnico: {str(e)}"

def obtener_resumen_pedidos():
    """Consulta trámites con limpieza de conexión."""
    try:
        connection.close_if_unusable_or_obsolete()
        hoy = timezone.now().date()
        envio_os_mes = Envio.objects.filter(tipo='os', fecha_solicitud__month=hoy.month).last()
        txt = "📋 *Estado de Gestión Mensual:*\n\n"
        if not envio_os_mes:
            txt += "⚠️ *Atención:* No iniciaste el trámite de OS este mes.\n\n"
        else:
            txt += f"✅ *Trámite OS:* {envio_os_mes.get_estado_display()}\n\n"
        
        pendientes = Envio.objects.filter(estado='tramite')
        if pendientes.exists():
            txt += "*En curso:*\n"
            for e in pendientes:
                txt += f"🔹 {e.tipo.upper()}: Hace {(hoy - e.fecha_solicitud.date()).days} días.\n"
        return txt
    except Exception as e:
        return f"Error en resumen: {e}"

# --- 4. CONFIGURACIÓN DE IA Y BOT ---



GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')

genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel(
    model_name='models/gemini-flash-latest', 
    tools=[consultar_estado_stock, registrar_movimiento, obtener_resumen_pedidos]
)

historiales = {}

async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in historiales:
        # Le pasamos un falso pasado donde la IA ya entendió el script del HTML
        historial_forzado = [
            {
                "role": "user", 
                "parts": ["Hola. Soy Astrana, gestionás el stock de MedChecked mediante herramientas. Reglas estrictas:\n1. NUNCA calcules stock a mano ni inventes números.\n2. Si te pido descargar CAJAS, usá tipo_stock='stock_normal'.\n3. Si te pido descargar UNIDADES sueltas o de backup, usá tipo_stock='seguridad'.\n4. Para 'Sondas', pasale el nombre 'Sonda' a la función."]
            },
            {
                "role": "model", 
                "parts": ["Entendido. Soy Astrana. Usaré las herramientas obligatoriamente. Para cajas usaré tipo_stock='stock_normal' y para unidades de seguridad usaré tipo_stock='seguridad'. No inventaré datos."]
            }
        ]
        historiales[user_id] = model.start_chat(history=historial_forzado, enable_automatic_function_calling=True)

    try:
        response = await asyncio.to_thread(historiales[user_id].send_message, update.message.text)
        
        if response.text:
            await update.message.reply_text(response.text)
        else:
            await update.message.reply_text("✅ Movimiento procesado en la base de datos.")
            
    except Exception as e:
        print(f"Error en respuesta: {e}")
        await update.message.reply_text("⚠️ Hubo un problema de conexión. ¿Probamos de nuevo?")


if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # Registramos solo el manejador de mensajes para responderte
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), responder))
    
    print("🚀 Astrana IA activa (Modo Reactivo Puro sin bucles)...")
    application.run_polling()