"""La hora del sistema: siempre la de Colombia, corra donde corra (el computador o un servidor en la nube, que trabaja en UTC)."""
from datetime import datetime, timedelta, timezone

COLOMBIA = timezone(timedelta(hours=-5))          # Colombia no tiene horario de verano


def hora_actual() -> datetime:
    """Ahora, en hora de Colombia y sin zona horaria, que es como se guardan las fechas en la base de datos."""
    return datetime.now(COLOMBIA).replace(tzinfo=None)
