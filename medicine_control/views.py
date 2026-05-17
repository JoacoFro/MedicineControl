from django.shortcuts import render, redirect
from .forms import PedidoForm, SalidaStockForm
from .models import Insumo, Pedido, Salida, Envio
from django.db.models import Sum, Q 
from datetime import datetime, timedelta
from django.utils.dateparse import parse_date
from django.utils import timezone
from django.http import JsonResponse
from .telegram_utils import enviar_alerta
import json
import requests
from django.http import JsonResponse
from django.utils import timezone
from .models import Insumo, Envio
import os

def home(request):
    insumos = Insumo.objects.all()
    
    # Cálculos base de stock
    total_unidades = sum(i.total_unidades_reales for i in insumos)
    total_backup_uds = sum(i.backup_unidades for i in insumos) # Reserva de seguridad en unidades
    
    # --- CORRECCIÓN DE CAJAS PARA QUE COINCIDA CON LA LISTA ---
    # Sumamos las cajas que vienen de la base de datos (Stock BNA: 12)
    cajas_bna = sum(i.stock_actual_cajas for i in insumos)
    # Definimos el total visual sumando la caja de reserva fija (Total: 13)
    total_cajas_visual = cajas_bna + 1 
    # ---------------------------------------------------------

    # Lógica de consumo IA
    ahora = datetime.now()
    hace_dos_semanas = ahora - timedelta(days=14)
    salidas_recientes = Salida.objects.filter(fecha__gte=hace_dos_semanas)
    unidades_consumidas = salidas_recientes.aggregate(Sum('cantidad'))['cantidad__sum'] or 0
    
    promedio_ia = unidades_consumidas / 14
    consumo_final = max(promedio_ia, 8) 
    
    # Estado de stock y autonomía
    techo_fijo = 400
    porcentaje = min((total_unidades / techo_fijo) * 100, 100)
    autonomia = int(total_unidades // consumo_final) if consumo_final > 0 else 0
    
    # Cálculo de fecha de próximo pedido (Día 15)
    if ahora.day >= 15:
        proximo_mes = ahora.replace(day=28) + timedelta(days=4)
        proximo_pedido = proximo_mes.replace(day=15)
    else:
        proximo_pedido = ahora.replace(day=15)

    # Verificación de trámites pendientes
    hay_os_pendiente = Envio.objects.filter(estado='tramite', tipo='os').exists()
    hay_backup_pendiente = Envio.objects.filter(estado='tramite', tipo='backup').exists()

    # Diccionario de contexto para el template
    context = {
        'total_unidades': total_unidades,                 # Las 444 unidades totales
        'total_normal_un': total_unidades - total_backup_uds, # Detalle: Normal
        'total_backup_un': total_backup_uds,             # Detalle: Seguridad
        
        'total_cajas': total_cajas_visual,               # El "13" grande del Inicio
        'total_cajas_normal': cajas_bna,                 # El "12" que dice "Stock BNA"
        
        'total_backup': total_backup_uds,                # El "84" de las Sondas sueltas
        'consumo_diario': round(consumo_final, 1),
        'autonomia': autonomia,
        'porcentaje': porcentaje,
        'proximo_pedido': proximo_pedido,
        'hay_os_pendiente': hay_os_pendiente,
        'hay_backup_pendiente': hay_backup_pendiente,
    }
    
    return render(request, 'medicine_control/home.html', context)

def cargar_insumo(request):
    if request.method == 'POST':
        insumo_default, _ = Insumo.objects.get_or_create(nombre="Sondas")
        tipo_destino = request.POST.get('tipo_stock')
        fecha = parse_date(request.POST.get('fecha'))
        lugar = request.POST.get('lugar_compra') or "Obra Social"
        
        if tipo_destino == 'stock_normal':
            cantidad = 300 
            insumo_default.stock_actual_cajas += 10
            tipo_para_db = 'normal'
        else:
            cantidad = int(request.POST.get('cantidad', 0))
            insumo_default.backup_unidades += cantidad
            tipo_para_db = 'propio'

        insumo_default.save()

        Pedido.objects.create(
            insumo=insumo_default,
            tipo=tipo_para_db,
            tipo_stock=tipo_destino,
            cantidad=cantidad,
            fecha=fecha,
            lugar_compra=lugar
        )
        return redirect('lista')
    return render(request, 'medicine_control/cargar_insumo.html')

def lista_insumos(request):
    # 1. Capturamos búsqueda
    query = request.GET.get('q', '').strip().lower()
    
    # 2. Datos Base (Métricas siempre globales)
    insumos_qs = Insumo.objects.all()
    pedidos_qs = Pedido.objects.all().order_by('-fecha') 
    salidas_qs = Salida.objects.all().order_by('-fecha')

    # --- CÁLCULOS GLOBALES ---
    total_unidades = sum(i.total_unidades_reales for i in insumos_qs)
    total_normal_un = sum(i.unidades_normales for i in insumos_qs)
    total_backup_un = sum(i.backup_unidades for i in insumos_qs)
    
    total_cajas_normal = sum(i.stock_actual_cajas for i in insumos_qs)
    
    # Lógica de Consumo IA (Promedio 14 días)
    hace_dos_semanas = datetime.now() - timedelta(days=14)
    salidas_recientes = Salida.objects.filter(fecha__gte=hace_dos_semanas)
    unidades_consumidas = salidas_recientes.aggregate(Sum('cantidad'))['cantidad__sum'] or 0
    
    promedio_ia = unidades_consumidas / 14
    consumo_final = max(promedio_ia, 8)
    
    # Cálculos de Autonomía Desglosada
    autonomia_total = int(total_unidades // consumo_final) if consumo_final > 0 else 0
    autonomia_normal = int(total_normal_un // consumo_final) if consumo_final > 0 else 0
    autonomia_backup = int(total_backup_un // consumo_final) if consumo_final > 0 else 0

    techo_fijo = 400
    porcentaje = min((total_unidades / techo_fijo) * 100, 100)

    # 3. FILTRADO (Solo afecta a las tablas de abajo)
    if query:
        if query in ['backup', 'reserva', 'seguridad', 'propio']:
            pedidos_qs = pedidos_qs.filter(tipo_stock='seguridad')
            salidas_qs = salidas_qs.filter(tipo_stock='seguridad')
        elif query in ['normal', 'os', 'obra social', 'bna']:
            pedidos_qs = pedidos_qs.filter(tipo_stock='stock_normal')
            salidas_qs = salidas_qs.filter(tipo_stock='stock_normal')
        else:
            pedidos_qs = pedidos_qs.filter(
                Q(lugar_compra__icontains=query) | 
                Q(tipo__icontains=query)
            )
            salidas_qs = salidas_qs.filter(tipo_stock__icontains=query)

    context = {
        'insumos': insumos_qs, 
        'ingresos': pedidos_qs, 
        'salidas': salidas_qs,
        # Métricas para Cards Desplegables
        'total_unidades': total_unidades,
        'total_normal_un': total_normal_un,
        'total_backup_un': total_backup_un,
        
        'total_cajas_normal': total_cajas_normal,
        
        'autonomia': autonomia_total,
        'aut_normal': autonomia_normal,
        'aut_backup': autonomia_backup,
        
        'consumo_diario': round(consumo_final, 1),
        'porcentaje': porcentaje,
        'query': query,
    }
    return render(request, 'medicine_control/lista_insumos.html', context)

def registrar_salida(request):
    if request.method == 'POST':
        insumo = Insumo.objects.get(nombre="Sondas")
        tipo_stock = request.POST.get('tipo_stock')
        cantidad_ingresada = int(request.POST.get('cantidad', 0))
        
        if tipo_stock == 'stock_normal':
            unidades_totales = cantidad_ingresada * 30
            insumo.stock_actual_cajas -= cantidad_ingresada
            cant_cajas_registro = cantidad_ingresada
        else:
            unidades_totales = cantidad_ingresada
            insumo.backup_unidades -= unidades_totales
            cant_cajas_registro = 0

        insumo.save()

        Salida.objects.create(
            insumo=insumo,
            cantidad_cajas=cant_cajas_registro,
            cantidad=unidades_totales,
            tipo_stock=tipo_stock
        )
        return redirect('lista')
    return render(request, 'medicine_control/salida_stock.html', {'insumo': Insumo.objects.first()})

def lista_envios(request):
    query = request.GET.get('q', '').strip().lower()
    envios = Envio.objects.all().order_by('-fecha_solicitud')

    if query:
        if query in ['backup', 'reserva', 'seguridad']:
            envios = envios.filter(tipo='backup')
        elif query in ['normal', 'obra social', 'os']:
            envios = envios.filter(tipo='os')
        else:
            envios = envios.filter(Q(estado__icontains=query) | Q(notas__icontains=query))

    recibidos = Envio.objects.filter(estado='recibido')
    promedio_demora = sum(e.demora_real for e in recibidos) / recibidos.count() if recibidos.exists() else 0

    return render(request, 'medicine_control/envios.html', {
        'envios': envios,
        'promedio_demora': round(promedio_demora, 1),
        'query': query 
    })

def iniciar_pedido(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body) if request.body else {}
            tipo_recibido = data.get('tipo', 'os')
            tipo_final = 'backup' if 'backup' in tipo_recibido.lower() or 'propio' in tipo_recibido.lower() else 'os'
            
            if Envio.objects.filter(estado='tramite', tipo=tipo_final).exists():
                return JsonResponse({'status': 'error', 'message': f'Ya existe un pedido de este tipo en curso.'}, status=400)

            Envio.objects.create(
                estado='tramite', 
                tipo=tipo_final,
                cantidad_pedida=12,
                notas=f"Iniciado desde Home"
            )
            return JsonResponse({'status': 'success', 'message': '¡Trámite iniciado!'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

def marcar_recibido_home(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            tipo_recibido = data.get('tipo', '').lower()
            tipo_final = 'backup' if 'backup' in tipo_recibido else 'os'
            pedido = Envio.objects.filter(estado='tramite', tipo=tipo_final).last()
            
            if pedido:
                pedido.estado = 'recibido'
                pedido.fecha_cierre = timezone.now() 
                pedido.save()
                return JsonResponse({'status': 'success', 'message': f'Trámite cerrado.'})
            return JsonResponse({'status': 'error', 'message': f'No hay trámite pendiente.'}, status=404)
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

def cron_monitoreo_sistema(request):
    # 1. Filtro de seguridad por token en la URL
    token = request.GET.get('token')
    if token != 'ClaveCronmedchecked':
        return JsonResponse({"error": "No autorizado"}, status=403)

    try:  # <--- Atajamos cualquier error interno
        hoy = timezone.now()
        es_viernes = (hoy.weekday() == 4)  # 4 representa el Viernes
        alertas = []
        
        # 2. EVALUACIÓN DE STOCK CRÍTICO
        insumos = Insumo.objects.all()
        for i in insumos:
            # Revisá si estos nombres de campos se llaman exactamente así en tu models.py
            if (i.stock_actual_cajas * 30) <= 30:
                alertas.append(f"📦 *O.S: Te queda {i.stock_actual_cajas} caja de {i.nombre} del stock base.")
            if i.backup_unidades <= 56:
                alertas.append(f"🛡️ *Seguridad: Te queda* {i.nombre} tiene solo {i.backup_unidades} un. de backup.")
            if i.autonomia_smart <= 10:
                alertas.append(f"🚨 *Crítico:* {i.nombre} con autonomía de {i.autonomia_smart} días.")

        # 3. CONSTRUCCIÓN DEL MENSAJE SEGÚN LAS REGLAS
        mensaje_final = ""
        
        if alertas:
            mensaje_final = "⚠️ *Joaco, tengo un ALERTA DE STOCK*\n\n" + "\n".join(alertas)
        
        if es_viernes:
            envio_os_mes = Envio.objects.filter(tipo='os', fecha_solicitud__month=hoy.month).last()
            txt_tramites = "\n\n📋 *Resumen de Gestión de Trámites:*\n"
            
            if not envio_os_mes:
                txt_tramites += "⚠️ *Atención Joaco:* No iniciaste el trámite de OS este mes.\n"
            else:
                txt_tramites += f"✅ *Trámite OS:* {envio_os_mes.get_estado_display()}\n"
                
            pendientes = Envio.objects.filter(estado='tramite')
            if pendientes.exists():
                txt_tramites += "\n*Trámites en curso:*\n"
                for e in pendientes:
                    txt_tramites += f"🔹 {e.tipo.upper()}: Hace {(hoy.date() - e.fecha_solicitud.date()).days} días.\n"
            
            mensaje_final += txt_tramites

        # 4. DISPARO DE TELEGRAM (Solo si hay algo que informar)
        if mensaje_final:
            BOT_TOKEN = os.getenv('TELEGRAM_TOKEN')  
            CHAT_ID = "8034926015"
            
            if not BOT_TOKEN:
                return JsonResponse({"error": "Configuración incompleta: Falta TELEGRAM_TOKEN"}, status=500)
                
            url_tg = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            
            requests.post(url_tg, json={
                "chat_id": CHAT_ID,
                "text": mensaje_final,
                "parse_mode": "Markdown"
            }, timeout=10)
            return JsonResponse({"status": "Mensaje enviado a Telegram con éxito."})

        return JsonResponse({"status": "Sin novedades en el frente. No se requería reporte."})

    except Exception as e:
        # Si algo se rompe, en vez de tirar 500 te muestra el error real en pantalla
        return JsonResponse({
            "error": "Error interno en la ejecución del código",
            "detalle_tecnico": str(e)
        }, status=500)