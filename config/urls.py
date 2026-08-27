from django.contrib import admin
from django.urls import path
from medicine_control import views # Importamos el módulo completo para ser más ordenados
from django.urls import path
from medicine_control.views import cron_monitoreo_sistema
from medicine_control.views import pastillero_view

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.home, name='home'),
    path('cargar/', views.cargar_insumo, name='cargar'),
    path('lista/', views.lista_insumos, name='lista'), 
    path('salida/', views.registrar_salida, name='salida_stock'), 
    path('envios/', views.lista_envios, name='envios'),
    path('iniciar-pedido/', views.iniciar_pedido, name='iniciar_pedido'), # Esta es la clave
    path('marcar-recibido/', views.marcar_recibido_home, name='marcar_recibido_home'),
    path('api/v1/sistema-monitoreo/', cron_monitoreo_sistema, name='monitoreo_sistema'),
path('pastillero/', views.pastillero_view, name='pastillero'),
]