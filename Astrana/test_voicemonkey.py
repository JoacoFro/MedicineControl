import requests

# Reemplaza con tu Token real de la web
MI_TOKEN = "f7a0c-30188-eb37b-5ef57-8760e-345da-f9-143143ea-7250-48cd-9609-8e110e5bfae8"

res = requests.post(
    'https://api-v3.voicemonkey.io/announce',
    json={
        'token': MI_TOKEN,
        'device': "astrana-aviso-d2axm", # El ID que te dio la web
        'speech': "Joaco, Astrana dice que el stock de sondas está bajo",
        'language': "es-US",
        'chime': "soundbank://soundlibrary/musical/amzn_sfx_electronic_beep_01",
    },
)

print(f"Respuesta de la API: {res.json()}")