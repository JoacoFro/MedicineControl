import os
import django

# Configurar el entorno de Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth.models import User

# Datos del superusuario (Podés cambiarlos acá o usarlos así)
username = 'MedChecked'
email = 'soportelabelbox@gmail.com'
password = '@Labelbox' # ¡Cambiá esto!

if not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username, email, password)
    print(f"Superusuario '{username}' creado con éxito.")
else:
    print(f"El usuario '{username}' ya existe.")