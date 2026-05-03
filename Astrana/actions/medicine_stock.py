# actions/medicine_stock.py

def gestionar_sondas(tipo_stock: str, operacion: str, cantidad: int):
    """
    Gestiona el inventario de sondas de Joaco.
    
    Args:
        tipo_stock: El tipo de stock a modificar. Debe ser 'cajas' (para obra social) o 'unidades' (para seguridad).
        operacion: La acción a realizar. Debe ser 'cargar' o 'descargar'.
        cantidad: La cantidad numérica de elementos.
    """
    
    # Por ahora, simulamos la acción para verificar que la IA entiende.
    # Más adelante, aquí irá el código que conecta con tu DB o Excel.
    
    resultado = f"Proceso de {operacion} de {cantidad} {tipo_stock} registrado con éxito."
    print(f"DEBUG ASTRANA: {resultado}")
    
    return {
        "status": "éxito",
        "mensaje_confirmacion": resultado
    }