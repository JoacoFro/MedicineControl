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

def registrar_movimiento(nombre_insumo: str, accion: str, cantidad: int, tipo_stock: str):
    """
    Registra la carga (pedido) o descarga (consumo) de insumos en el sistema.
    Argumentos:
        nombre_insumo: Nombre del producto (ej: 'Sonda', 'Gasa', etc.)
        accion: 'cargar' o 'descargar'
        tipo_stock: 'cajas' (para stock_normal) o 'unidades' (para seguridad)
    """
    try:
        connection.close_if_unusable_or_obsolete()
        
        # Limpieza de plurales
        nombre_busqueda = nombre_insumo.rstrip('sS') 
        insumo = Insumo.objects.filter(nombre__icontains=nombre_busqueda).first()
        
        if not insumo:
            return f"❌ ERROR: No encontré el insumo '{nombre_insumo}'."

        ahora = timezone.now()
        tipo_usado = ""

        # --- LÓGICA DE DESCARGA (CONSUMOS / SALIDAS) ---
        if accion == "descargar":
            if tipo_stock in ["stock_normal", "cajas", "principal", "normal"]:
                insumo.stock_actual_cajas -= cantidad
                Salida.objects.create(
                    insumo=insumo, 
                    cantidad_cajas=amount, # cantidad_cajas
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

        # --- LÓGICA DE CARGA (PEDIDOS / INGRESOS) --- ¡ESTO FALTABA!
        elif accion == "cargar":
            if tipo_stock in ["stock_normal", "cajas", "principal", "normal"]:
                insumo.stock_actual_cajas += cantidad
                Pedido.objects.create(
                    insumo=insumo,
                    tipo='normal',
                    tipo_stock='stock_normal',
                    cantidad=cantidad * 30, # Convierte cajas a unidades para el historial de Pedidos
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
    Inicia un trámite mensual ('os' o 'backup') y registra la cantidad pedida en el sistema.
    Argumentos:
        tipo_tramite: 'os' o 'backup'.
        cantidad: Cantidad de insumos/cajas que se van a solicitar (obligatorio/preguntar).
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
            
        # Si la IA no entendió la cantidad en el mensaje, frena el flujo y la pregunta
        if cantidad is None or cantidad <= 0:
            return f"❓ ¿Cuántas cajas o unidades vas a solicitar para el trámite de {nombre_legible}?"
            
        hoy = timezone.now()
        
        # Evitamos duplicados activos en el mismo mes y año
        tramite_existente = Envio.objects.filter(
            tipo=tipo_final, 
            estado='tramite',
            fecha_solicitud__month=hoy.month,
            fecha_solicitud__year=hoy.year
        ).exists()
        
        if tramite_existente:
            return f"⚠️ ATENCIÓN: Ya existe un trámite de {nombre_legible} en curso para este mes."
            
        # Creamos el registro usando el campo real de tu modelo: 'cantidad_pedida'
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
    Cierra un trámite activo ('os' o 'backup') pasándolo a 'recibido'.
    Recupera de forma automática la 'cantidad_pedida' inicial, suma el stock físico 
    en la tabla Insumo e impacta la tabla de ingresos Pedido.
    Argumentos:
        tipo_tramite: 'os' o 'backup'.
        tipo_stock: 'cajas' (stock normal) o 'unidades' (seguridad).
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
            
        # Buscamos el trámite activo más reciente
        tramite = Envio.objects.filter(tipo=tipo_final, estado='tramite').last()
        if not tramite:
            return f"⚠️ No encontré ningún trámite activo de {nombre_legible} en curso para cerrar."
            
        # LEEMOS LA CANTIDAD QUE GUARDAMOS AL INICIO
        cantidad = tramite.cantidad_pedida
        ahora = timezone.now()
        
        # Cambiamos el estado administrativo según los choices reales de tu modelo ('recibido')
        tramite.estado = 'recibido'
        tramite.fecha_cierre = ahora.date()
        tramite.save()
        
        resultado_msg = f"📋 ¡Trámite de {nombre_legible} cerrado con éxito! Estado: 'Recibido'."

        # LÓGICA DE IMPACTO REAL
        if cantidad and cantidad > 0:
            insumo = Insumo.objects.filter(nombre__icontains=insumo_defecto).first()
            if not insumo:
                return resultado_msg + f" ⚠️ Trámite cerrado, pero no encontré el insumo '{insumo_defecto}' en el sistema para actualizar stock."
            
            # Si se guarda en Stock Normal (Cajas)
            if tipo_stock in ["stock_normal", "cajas", "principal", "normal"]:
                insumo.stock_actual_cajas += cantidad
                
                # Creamos el registro en la tabla de ingresos (Pedido) usando tus choices reales
                Pedido.objects.create(
                    insumo=insumo,
                    tipo=tipo_final,          # Guarda 'os' o 'backup'
                    tipo_stock='stock_normal', # Elección del destino
                    cantidad=cantidad,         # Registramos las cajas que ingresaron
                    fecha=ahora.date(),
                    lugar_compra=f"Cierre Trámite {nombre_legible}"
                )
                detalle_stock = f"Se sumaron {cantidad} cajas al Stock Normal."
                
            # Si se guarda en Reserva / Seguridad (Unidades)
            else:
                insumo.backup_unidades += cantidad
                
                # Creamos el registro en la tabla de ingresos (Pedido)
                Pedido.objects.create(
                    insumo=insumo,
                    tipo=tipo_final,       # Guarda 'os' o 'backup'
                    tipo_stock='seguridad', # Elección del destino
                    cantidad=cantidad,      # Registramos las unidades que ingresaron
                    fecha=ahora.date(),
                    lugar_compra=f"Cierre Trámite {nombre_legible}"
                )
                detalle_stock = f"Se sumaron {cantidad} unidades al Stock de Seguridad."
                
            insumo.save()
            insumo.refresh_from_db()
            
            resultado_msg += f"\n📦 ¡Base de datos actualizada! {detalle_stock}\nNuevo total real disponible: {insumo.total_unidades_reales} un."
        else:
            resultado_msg += f"\n⚠️ El trámite se cerró, pero la cantidad pedida registrada era {cantidad}, por lo que no se modificó el stock."

        return resultado_msg

    except Exception as e:
        return f"❌ Error técnico al procesar el cierre e impacto: {str(e)}"
     
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
                # CORRECCIÓN: Se cambió e.fecha_solicitud.date() por e.fecha_solicitud
                txt += f"🔹 {e.tipo.upper()}: Hace {(hoy - e.fecha_solicitud).days} días.\n"
        return txt
    except Exception as e:
        return f"Error en resumen: {e}"

# --- 4. CONFIGURACIÓN DE IA Y BOT ---



GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')

genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel(
    model_name='models/gemini-flash-latest', 
    tools=[consultar_estado_stock, registrar_movimiento, obtener_resumen_pedidos,iniciar_tramite_pedido, cerrar_tramite_pedido]
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