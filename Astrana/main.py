import os
import sys
import django
import logging
import asyncio
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
import google.generativeai as genai
from django.utils import timezone
from datetime import datetime

# --- 1. PUENTE CON DJANGO ---
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

load_dotenv(BASE_DIR / ".env")
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from medicine_control.models import Insumo, Pedido, Salida

# --- 2. FUNCIONES DE LÓGICA (TOOLS PARA LA IA) ---

def consultar_estado_stock():
    """Consulta el stock detallado de todos los insumos y su autonomía."""
    try:
        insumos = Insumo.objects.all()
        if not insumos:
            return "No hay insumos registrados en la base de datos."
        
        reporte = "📊 Estado Actual:\n"
        for i in insumos:
            aut = i.autonomia_smart
            emoji = "🟢" if aut >= 15 else "🟡" if aut >= 7 else "🔴"
            reporte += (f"- {i.nombre}: {i.total_unidades_reales} unidades "
                        f"({i.stock_actual_cajas} cajas, {i.backup_unidades} backup). "
                        f"Autonomía: {emoji} {aut} días.\n")
        return reporte
    except Exception as e:
        return f"Error al consultar stock: {e}"

def registrar_movimiento(accion: str, cantidad: int, tipo_stock: str):
    """
    Registra una carga o descarga de insumos.
    accion: 'cargar' o 'descargar'
    cantidad: número entero
    tipo_stock: 'cajas' (para Obra Social) o 'unidades' (para Seguridad/Backup)
    """
    try:
        insumo = Insumo.objects.filter(nombre__icontains="Sonda").first()
        if not insumo:
            return "Error: No encontré el insumo 'Sonda'."

        ahora = timezone.now()
        
        if accion == "cargar":
            if tipo_stock == "cajas":
                insumo.stock_actual_cajas += cantidad
                unidades = cantidad * 30
                Pedido.objects.create(insumo=insumo, tipo='normal', tipo_stock='stock_normal', 
                                     cantidad=unidades, fecha=ahora, lugar_compra="Astrana IA (Cajas)")
            else:
                insumo.backup_unidades += cantidad
                Pedido.objects.create(insumo=insumo, tipo='propio', tipo_stock='seguridad', 
                                     cantidad=cantidad, fecha=ahora, lugar_compra="Astrana IA (Backup)")
        
        elif accion == "descargar":
            if tipo_stock == "cajas":
                insumo.stock_actual_cajas -= cantidad
                Salida.objects.create(insumo=insumo, cantidad_cajas=cantidad, 
                                     cantidad=cantidad*30, tipo_stock='stock_normal')
            else:
                insumo.backup_unidades -= cantidad
                Salida.objects.create(insumo=insumo, cantidad_cajas=0, 
                                     cantidad=cantidad, tipo_stock='seguridad')

        insumo.save()
        return (f"✅ Registro exitoso. Nuevo total de {insumo.nombre}: {insumo.total_unidades_reales} unidades. "
                f"Estado: {insumo.semaforo_estado}")
    except Exception as e:
        return f"Error técnico: {e}"
    
def gestionar_tramites_os(accion, nombre_insumo, tipo_tramite='os'):
    try:
        from medicine_control.models import Envio, Insumo
        from django.utils import timezone
        
        envio = Envio.objects.filter(estado='tramite', tipo=tipo_tramite).last()

        if not envio:
            return f"No encontré ningún trámite de {tipo_tramite} pendiente."

        if accion == "marcar_recibido":
            # Buscamos el insumo para sumarle las unidades
            insumo = Insumo.objects.get(nombre__icontains=nombre_insumo)
            
            if tipo_tramite == 'os':
                # Si es OS, sumamos cajas (10 cajas por defecto)
                insumo.stock_actual_cajas += 10
                detalle = "10 cajas al Stock Normal"
            else:
                # Si es Backup, sumamos a backup_unidades lo que dice el envío
                unidades = envio.cantidad_pedida
                insumo.backup_unidades += unidades
                detalle = f"{unidades} unidades al Stock de Backup"

            insumo.save()

            # Cerramos el trámite
            envio.estado = 'recibido'
            envio.fecha_cierre = timezone.now().date()
            envio.save()
            
            return f"🟢 ¡Recibido! Se sumaron {detalle} a {insumo.nombre}."
            
    except Exception as e:
        return f"Error al gestionar trámite: {e}"
    
def obtener_resumen_pedidos():
    """
    Resumen definitivo: Consulta la tabla Envio (web) y maneja fechas correctamente.
    """
    try:
        from medicine_control.models import Envio
        hoy = timezone.now().date()
        
        # 1. Buscamos el trámite de OS de este mes para el "Doble Check"
        envio_os_mes = Envio.objects.filter(
            tipo='os',
            fecha_solicitud__month=hoy.month,
            fecha_solicitud__year=hoy.year
        ).last()

        txt = "📋 *Estado de Gestión Mensual:*\n\n"
        
        # Lógica de validación contra el calendario (Día 15)
        if not envio_os_mes:
            if hoy.day <= 15:
                txt += f"⚠️ *Atención:* No iniciaste el trámite de OS (Límite: 15/{hoy.month}).\n\n"
            else:
                txt += f"🚨 *Alerta:* Pasó el día 15 y no hay trámite de OS registrado.\n\n"
        else:
            # Aquí corregimos lo que me decías: ¿Está abierto o completado?
            if envio_os_mes.estado == 'recibido':
                txt += "✅ *Trámite OS:* Ya fue iniciado y RECIBIDO correctamente. 📦\n\n"
            else:
                txt += "⏳ *Trámite OS:* Iniciado, pero figura PENDIENTE de recepción.\n\n"

        # 2. Listado de lo que está "En Trámite" actualmente (los botones verdes de la web)
        pendientes_web = Envio.objects.filter(estado='tramite')

        if not pendientes_web.exists():
            txt += "No hay otros trámites pendientes en el panel web. ✅"
        else:
            txt += "*Otros envíos en curso:*\n"
            for e in pendientes_web:
                # Evitamos el error de 'datetime.date' object has no attribute 'date'
                f_sol = e.fecha_solicitud.date() if hasattr(e.fecha_solicitud, 'date') else e.fecha_solicitud
                dias = (hoy - f_sol).days
                
                tipo_txt = "🛡️ Back Up" if e.tipo == 'backup' else "📦 Obra Social"
                txt += (f"🔹 {tipo_txt}\n"
                        f"   • Estado: {e.get_estado_display()}\n"
                        f"   • Hace: {dias} días.\n")
        
        return txt
    except Exception as e:
        return f"Error en resumen: {e}"
def iniciar_tramite_backup(tipo_insumo, cantidad):
    try:
        from medicine_control.models import Envio
        
        # Como Envio no tiene FK a Insumo, guardamos el nombre en las notas
        nuevo_envio = Envio.objects.create(
            tipo='backup',
            estado='tramite',
            cantidad_pedida=int(cantidad),
            notas=f"Pedido de Backup para: {tipo_insumo}"
        )
        return f"✅ Trámite de Backup iniciado por {cantidad} unidades."
    except Exception as e:
        return f"Error al iniciar: {e}"
    
# --- 3. CONFIGURACIÓN DE IA ---
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    model_name='models/gemini-flash-latest', 
    tools=[consultar_estado_stock, registrar_movimiento, gestionar_tramites_os, obtener_resumen_pedidos]
)

# --- 4. LÓGICA DEL BOT ---
historiales = {}

async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    
    if user_id not in historiales:
        prompt_sistema = (
            "Sos Astrana, asistente de la IA de Joaco. Tenés acceso al sistema MedicineControl.\n\n"
            "INSTRUCCIONES DE USO DE HERRAMIENTAS:\n"
            "1. Si Joaco pregunta 'cómo estamos' o sobre el stock, usá 'consultar_estado_stock'.\n"
            "2. Si quiere cargar/descargar stock manualmente, usá 'registrar_movimiento'.\n"
            "3. IMPORTANTE: Si Joaco dice 'inicia un trámite' o 'empezar gestión de OS', usá 'gestionar_tramites_os' con accion='iniciar_tramite'.\n"
            "4. Si Joaco dice 'recibí el pedido' o 'llegaron las cajas', usá 'gestionar_tramites_os' con accion='marcar_recibido'.\n"
            "5. Si pregunta por pedidos pendientes o el resumen mensual, usá 'obtener_resumen_pedidos'.\n\n"
            "Mantené un tono profesional pero cercano. Siempre que realices una acción, confirma que se impactó en el sistema."
        )
        historiales[user_id] = model.start_chat(
            history=[{"role": "user", "parts": [prompt_sistema]},
                     {"role": "model", "parts": ["Entendido Joaco. Sistema y herramientas de trámites sincronizadas. ¿Qué gestión iniciamos?"]}],
            enable_automatic_function_calling=True
        )

    try:
        response = await asyncio.to_thread(historiales[user_id].send_message, user_text)
        await update.message.reply_text(response.text)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), responder))
    print("🚀 Astrana IA (Versión Evolucionada) iniciando...")
    application.run_polling()
    
