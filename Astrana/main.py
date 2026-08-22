import os
import sys
import asyncio
import threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

import django
from django.db import connection
from django.utils import timezone
from asgiref.sync import sync_to_async

# --- 1. PUENTE Y CONFIGURACIÓN CON DJANGO ---
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

load_dotenv(BASE_DIR / ".env")
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from medicine_control.models import Insumo, Pedido, Salida, Envio
import google.generativeai as genai

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# --- 2. SERVIDOR DUMMY HTTP PARA RENDER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Astrana Bot Activo y Saludable")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 3. FUNCIONES DE LÓGICA / HERRAMIENTAS DJANGO ---

def consultar_estado_stock():
    """Consulta el stock detallado limpiando la conexión SSL."""
    try:
        connection.close_if_unusable_or_obsolete()
        insumos = Insumo.objects.all()
        if not insumos:
            return "No hay insumos registrados en la base de datos."
        
        reporte = "📊 **Estado Actual del Stock:**\n"
        for i in insumos:
            aut = i.autonomia_smart
            emoji = "🔴" if aut <= 10 else "🟡" if aut <= 15 else "🟢"
            reporte += (f"• **{i.nombre}**: {i.total_unidades_reales} un. "
                        f"({i.stock_actual_cajas} cajas, {i.backup_unidades} backup). "
                        f"Autonomía: {emoji} {aut} días.\n")
        return reporte
    except Exception as e:
        return f"Error al consultar stock: {e}"

def registrar_movimiento(nombre_insumo: str, accion: str, cantidad: int, tipo_stock: str):
    """
    Registra la carga (pedido) o descarga (consumo) de insumos en el sistema.
    Argumentos:
        nombre_insumo: Nombre del producto (ej: 'Sonda', etc.)
        accion: 'cargar' o 'descargar'
        tipo_stock: 'cajas' (para stock_normal) o 'unidades' (para seguridad)
    """
    try:
        connection.close_if_unusable_or_obsolete()
        
        nombre_busqueda = nombre_insumo.rstrip('sS') 
        insumo = Insumo.objects.filter(nombre__icontains=nombre_busqueda).first()
        
        if not insumo:
            return f"❌ ERROR: No encontré el insumo '{nombre_insumo}'."

        ahora = timezone.now()
        tipo_usado = ""

        if accion == "descargar":
            if tipo_stock in ["stock_normal", "cajas", "principal", "normal"]:
                insumo.stock_actual_cajas -= cantidad
                Salida.objects.create(
                    insumo=insumo, 
                    cantidad_cajas=cantidad, 
                    cantidad=cantidad * 30, 
                    tipo_stock='stock_normal'
                )
                tipo_usado = "Descarga de Stock Normal (Cajas)"
            else:
                insumo.backup_unidades -= cantidad
                Salida.objects.create(
                    insumo=insumo, 
                    cantidad_cajas=0, 
                    cantidad=cantidad, 
                    tipo_stock='seguridad'
                )
                tipo_usado = "Descarga de Stock de Seguridad (Unidades)"

        elif accion == "cargar":
            if tipo_stock in ["stock_normal", "cajas", "principal", "normal"]:
                insumo.stock_actual_cajas += cantidad
                Pedido.objects.create(
                    insumo=insumo,
                    tipo='normal',
                    tipo_stock='stock_normal',
                    cantidad=cantidad * 30,
                    fecha=ahora,
                    lugar_compra="Astrana IA"
                )
                tipo_usado = "Carga de Stock Normal (Cajas)"
            else:
                insumo.backup_unidades += cantidad
                Pedido.objects.create(
                    insumo=insumo,
                    tipo='propio',
                    tipo_stock='seguridad',
                    cantidad=cantidad,
                    fecha=ahora,
                    lugar_compra="Astrana IA"
                )
                tipo_usado = "Carga de Stock de Seguridad (Unidades)"
        else:
            return f"❌ ERROR: Acción '{accion}' no reconocida. Usar 'cargar' o 'descargar'."

        insumo.save()
        insumo.refresh_from_db()
        return f"✅ Operación exitosa: {tipo_usado} para {insumo.nombre}. Cantidad: {cantidad}. Nuevo total real: {insumo.total_unidades_reales} un."

    except Exception as e:
        return f"❌ Error técnico: {str(e)}"

def iniciar_tramite_pedido(tipo_tramite: str, cantidad: int = None):
    """
    Inicia un trámite mensual ('os' o 'backup') y registra la cantidad pedida.
    """
    try:
        connection.close_if_unusable_or_obsolete()
        
        tipo_normalizado = tipo_tramite.lower().strip()
        if tipo_normalizado in ["obra social", "os", "social"]:
            tipo_final = "os"
            nombre_legible = "Obra Social"
        elif tipo_normalizado in ["backup", "seguridad", "propio"]:
            tipo_final = "backup"
            nombre_legible = "Backup"
        else:
            return f"❌ ERROR: El tipo de trámite '{tipo_tramite}' no es válido."
            
        if cantidad is None or cantidad <= 0:
            return f"❓ ¿Cuántas cajas o unidades vas a solicitar para el trámite de {nombre_legible}?"
            
        hoy = timezone.now()
        tramite_existente = Envio.objects.filter(
            tipo=tipo_final, 
            estado='tramite',
            fecha_solicitud__month=hoy.month,
            fecha_solicitud__year=hoy.year
        ).exists()
        
        if tramite_existente:
            return f"⚠️ ATENCIÓN: Ya existe un trámite de {nombre_legible} en curso para este mes."
            
        nuevo_envio = Envio.objects.create(
            tipo=tipo_final,
            estado='tramite',
            cantidad_pedida=cantidad
        )
        return f"📋 ¡Trámite de {nombre_legible} Iniciado! Registrado con una solicitud de {cantidad} cajas/unidades."

    except Exception as e:
        return f"❌ Error técnico al iniciar trámite: {str(e)}"

def cerrar_tramite_pedido(tipo_tramite: str, tipo_stock: str = "cajas"):
    """
    Cierra un trámite activo pasándolo a 'recibido' e impacta el stock.
    """
    try:
        connection.close_if_unusable_or_obsolete()
        
        tipo_normalizado = tipo_tramite.lower().strip()
        if tipo_normalizado in ["obra social", "os", "social"]:
            tipo_final = "os"
            nombre_legible = "Obra Social"
            insumo_defecto = "Sonda"
        elif tipo_normalizado in ["backup", "seguridad", "propio"]:
            tipo_final = "backup"
            nombre_legible = "Backup"
            insumo_defecto = "Sonda"
        else:
            return f"❌ ERROR: Tipo de trámite '{tipo_tramite}' no reconocido."
            
        tramite = Envio.objects.filter(tipo=tipo_final, estado='tramite').last()
        if not tramite:
            return f"⚠️ No encontré ningún trámite activo de {nombre_legible} en curso para cerrar."
            
        cantidad = tramite.cantidad_pedida
        ahora = timezone.now()
        
        tramite.estado = 'recibido'
        tramite.fecha_cierre = ahora.date()
        tramite.save()
        
        resultado_msg = f"📋 ¡Trámite de {nombre_legible} cerrado con éxito! Estado: 'Recibido'."

        if cantidad and cantidad > 0:
            insumo = Insumo.objects.filter(nombre__icontains=insumo_defecto).first()
            if not insumo:
                return resultado_msg + f" ⚠️ Trámite cerrado, pero no encontré el insumo '{insumo_defecto}' para actualizar stock."
            
            if tipo_stock in ["stock_normal", "cajas", "principal", "normal"]:
                insumo.stock_actual_cajas += cantidad
                Pedido.objects.create(
                    insumo=insumo,
                    tipo=tipo_final,
                    tipo_stock='stock_normal',
                    cantidad=cantidad,
                    fecha=ahora.date(),
                    lugar_compra=f"Cierre Trámite {nombre_legible}"
                )
                detalle_stock = f"Se sumaron {cantidad} cajas al Stock Normal."
            else:
                insumo.backup_unidades += cantidad
                Pedido.objects.create(
                    insumo=insumo,
                    tipo=tipo_final,
                    tipo_stock='seguridad',
                    cantidad=cantidad,
                    fecha=ahora.date(),
                    lugar_compra=f"Cierre Trámite {nombre_legible}"
                )
                detalle_stock = f"Se sumaron {cantidad} unidades al Stock de Seguridad."
                
            insumo.save()
            insumo.refresh_from_db()
            resultado_msg += f"\n📦 ¡Base de datos actualizada! {detalle_stock}\nNuevo total real disponible: {insumo.total_unidades_reales} un."
        else:
            resultado_msg += f"\n⚠️ El trámite se cerró, pero no tenía cantidad pedida registrada."

        return resultado_msg

    except Exception as e:
        return f"❌ Error técnico al procesar el cierre: {str(e)}"

def obtener_resumen_pedidos():
    """Consulta los trámites actuales en curso."""
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
                txt += f"🔹 {e.tipo.upper()}: Hace {(hoy - e.fecha_solicitud).days} días.\n"
        return txt
    except Exception as e:
        return f"Error en resumen: {e}"

# --- 4. CONFIGURACIÓN DE GEMINI Y BOT ---
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name='models/gemini-flash-latest', 
        tools=[consultar_estado_stock, registrar_movimiento, obtener_resumen_pedidos, iniciar_tramite_pedido, cerrar_tramite_pedido]
    )

historiales = {}

# --- 5. MENÚS INTERACTIVOS CON BOTONES ---
async def enviar_menu_opciones(update: Update, context: ContextTypes.DEFAULT_TYPE, saludo: str):
    keyboard = [
        [
            InlineKeyboardButton("📦 Consultar Stock", callback_data="op_stock"),
            InlineKeyboardButton("📋 Estado de Trámites", callback_data="op_tramites"),
        ],
        [
            InlineKeyboardButton("➕ Iniciar Trámite OS", callback_data="op_tramite_os"),
            InlineKeyboardButton("🔄 Iniciar Trámite Backup", callback_data="op_tramite_backup"),
        ],
        [
            InlineKeyboardButton("💬 Hablar libremente con IA", callback_data="op_chat"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(saludo, reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.reply_text(saludo, reply_markup=reply_markup)

async def manejar_botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await sync_to_async(connection.close_if_unusable_or_obsolete)()
    opcion = query.data

    if opcion == "op_stock":
        res = await sync_to_async(consultar_estado_stock)()
        await query.edit_message_text(res, parse_mode="Markdown")
        
    elif opcion == "op_tramites":
        res = await sync_to_async(obtener_resumen_pedidos)()
        await query.edit_message_text(res, parse_mode="Markdown")

    elif opcion == "op_tramite_os":
        res = await sync_to_async(iniciar_tramite_pedido)(tipo_tramite="os", cantidad=12)
        await query.edit_message_text(res, parse_mode="Markdown")

    elif opcion == "op_tramite_backup":
        res = await sync_to_async(iniciar_tramite_pedido)(tipo_tramite="backup", cantidad=150)
        await query.edit_message_text(res, parse_mode="Markdown")

    elif opcion == "op_chat":
        await query.edit_message_text("💬 **Modo Chat con IA Activado:**\nPodés escribirme cualquier consulta libremente.")

# --- 6. ATENCIÓN DE MENSAJES Y CHAT ---
async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto_usuario = update.message.text.strip()
    texto_lower = texto_usuario.lower()

    # Disparador para mostrar el menú
    if "hola astrana" in texto_lower or texto_lower in ["/start", "/menu"]:
        await enviar_menu_opciones(update, context, "Hola Joaco, ¿cómo te ayudo?")
        return

    # Si es texto libre, consulta a la IA con herramientas
    user_id = update.effective_user.id
    
    if user_id not in historiales and GEMINI_API_KEY:
        historial_forzado = [
            {
                "role": "user", 
                "parts": ["Hola. Soy Astrana, gestionás el stock mediante herramientas. Reglas estrictas:\n1. NUNCA calcules stock a mano ni inventes números.\n2. Si te pido descargar CAJAS, usá tipo_stock='stock_normal'.\n3. Si te pido descargar UNIDADES sueltas o de backup, usá tipo_stock='seguridad'.\n4. Para 'Sondas', pasale el nombre 'Sonda' a la función."]
            },
            {
                "role": "model", 
                "parts": ["Entendido. Soy Astrana. Usaré las herramientas obligatoriamente. Para cajas usaré tipo_stock='stock_normal' y para unidades de seguridad usaré tipo_stock='seguridad'."]
            }
        ]
        historiales[user_id] = model.start_chat(history=historial_forzado, enable_automatic_function_calling=True)

    try:
        await sync_to_async(connection.close_if_unusable_or_obsolete)()
        response = await asyncio.to_thread(historiales[user_id].send_message, texto_usuario)
        
        if response.text:
            await update.message.reply_text(response.text)
        else:
            await update.message.reply_text("✅ Movimiento procesado en la base de datos.")
            
    except Exception as e:
        print(f"Error en respuesta IA: {e}")
        await update.message.reply_text("⚠️ Hubo un problema al procesar el mensaje. Probá diciendo 'Hola Astrana'.")

# --- 7. PUNTO DE ENTRADA ---
def main():
    if not TELEGRAM_TOKEN:
        print("❌ ERROR: No se encontró TELEGRAM_TOKEN.")
        return

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler(["start", "menu"], responder))
    application.add_handler(CallbackQueryHandler(manejar_botones))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), responder))
    
    print("🚀 Astrana IA (Híbrido Menú + Herramientas) desplegando...")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()