from django.db import models
from django.db.models import Sum
from django.utils import timezone
from datetime import datetime, timedelta

class Insumo(models.Model):
    nombre = models.CharField(max_length=100)
    stock_actual_cajas = models.IntegerField() 
    unidades_por_caja = models.IntegerField(default=30)
    consumo_diario = models.FloatField(default=0) 
    backup_unidades = models.IntegerField(default=0)

    @property
    def total_unidades_reales(self):
        return (self.stock_actual_cajas * self.unidades_por_caja) + self.backup_unidades

    # --- NUEVAS PROPIEDADES PARA EL DESGLOSE ---
    
    @property
    def unidades_normales(self):
        """Solo lo que hay en cajas."""
        return self.stock_actual_cajas * self.unidades_por_caja

    @property
    def autonomia_normal_dias(self):
        """Autonomía sin contar el backup."""
        consumo = self.consumo_diario if self.consumo_diario > 0 else 8
        return int(self.unidades_normales // consumo)

    @property
    def autonomia_seguridad_dias(self):
        """Autonomía exclusivamente del backup."""
        consumo = self.consumo_diario if self.consumo_diario > 0 else 8
        return int(self.backup_unidades // consumo)

    @property
    def autonomia_smart(self):
        """Total (Normal + Seguridad)."""
        return self.autonomia_normal_dias + self.autonomia_seguridad_dias

    @property
    def semaforo_estado(self):
        dias = self.autonomia_smart
        if dias > 40: return "OPTIMO"
        if dias > 20: return "ESTABLE"
        if dias > 10: return "ALERTA"
        return "CRITICO"

    def __str__(self):
        return self.nombre

class Pedido(models.Model):
    TIPO_CHOICES = [
        ('os', 'Obra Social'),
        ('backup', 'Reserva / Backup'),
    ]
    DESTINO_CHOICES = [('stock_normal', 'Stock Normal'), ('seguridad', 'Reserva / Seguridad')]
    
    insumo = models.ForeignKey(Insumo, on_delete=models.CASCADE)
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, default='os')
    tipo_stock = models.CharField(max_length=20, choices=DESTINO_CHOICES, default='stock_normal')
    cantidad = models.IntegerField() 
    fecha = models.DateField()
    lugar_compra = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return f"{self.insumo.nombre} - {self.get_tipo_stock_display()} ({self.fecha})"

class Salida(models.Model):
    insumo = models.ForeignKey(Insumo, on_delete=models.CASCADE)
    cantidad_cajas = models.PositiveIntegerField(default=0)
    cantidad = models.PositiveIntegerField() 
    tipo_stock = models.CharField(max_length=20) 
    fecha = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Salida de {self.cantidad} unidades de {self.insumo.nombre}"

class HistorialMovimiento(models.Model):
    TIPO_CHOICES = [('INGRESO', 'Ingreso'), ('SALIDA', 'Salida')]
    STOCK_CHOICES = [('NORMAL', 'Caja Normal'), ('BACKUP', 'Stock de Seguridad')]
    insumo = models.ForeignKey(Insumo, on_delete=models.CASCADE)
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    tipo_stock = models.CharField(max_length=10, choices=STOCK_CHOICES)
    cantidad_unidades = models.IntegerField()
    fecha = models.DateTimeField(auto_now_add=True)

class Envio(models.Model):
    ESTADOS = [
        ('tramite', 'En Trámite'),
        ('recibido', 'Recibido'),
        ('pendiente', 'Pendiente'),
    ]
    
    # Definimos los tipos fijos
    TIPOS = [
        ('os', 'Obra Social'),
        ('backup', 'Back Up / Propio'),
    ]
    
    fecha_solicitud = models.DateField(auto_now_add=True)
    fecha_cierre = models.DateField(null=True, blank=True)
    cantidad_pedida = models.IntegerField(default=12)
    # Cambiamos a choices para tener control total
    tipo = models.CharField(max_length=10, choices=TIPOS, default='os') 
    estado = models.CharField(max_length=20, choices=ESTADOS, default='tramite')
    notas = models.TextField(blank=True, null=True)

    @property
    def demora_real(self):
        if self.fecha_cierre:
            return (self.fecha_cierre - self.fecha_solicitud).days
        return (timezone.now().date() - self.fecha_solicitud).days

    def __str__(self):
        return f"{self.get_tipo_display()} - {self.get_estado_display()}"
    # medicine_control/models.py

class Pastillero(models.Model):
    insumo = models.ForeignKey(Insumo, on_delete=models.CASCADE, related_name='tomas_pastillero')
    fecha_hora = models.DateTimeField(default=timezone.now)
    cantidad = models.IntegerField(default=1, help_text="Cantidad de comprimidos/unidades tomadas")
    
    class Meta:
        ordering = ['-fecha_hora']
        verbose_name = "Registro de Pastillero"
        verbose_name_plural = "Pastillero"

    def __str__(self):
        return f"{self.insumo.nombre} - {self.cantidad} un. ({self.fecha_hora.strftime('%d/%m/%Y %H:%M')})"