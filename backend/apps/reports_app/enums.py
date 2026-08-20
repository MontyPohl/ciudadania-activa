from django.db import models


class MediaType(models.TextChoices):
    """Tipo de archivo adjunto a un reporte (Anexo A.1).

    El MVP solo persiste fotografías del ciudadano en `report_media`: las
    evidencias de resolución tienen su propia tabla (`resolution_evidences`).
    """

    FOTO = "FOTO", "Foto"
