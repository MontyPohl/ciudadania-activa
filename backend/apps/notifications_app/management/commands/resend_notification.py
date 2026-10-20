"""Reintento **manual** de una notificación que quedó en FALLIDO (Sprint 4, ADR-0015).

Es la única forma de reenviar un correo fallido, y es deliberadamente incómoda: hay que
entrar por consola y nombrar la notificación una por una. No existe endpoint ni acción en
el admin. Los reintentos automáticos del Sprint 13 solo actúan mientras la notificación
sigue PENDIENTE; FALLIDO sigue siendo terminal para todo lo automático, y este comando
—que reinicia el contador de intentos— es su única recuperación (CT-51).

Uso:
    python manage.py resend_notification <notification_uuid>

El comando no envía nada por sí mismo: devuelve la notificación a PENDIENTE, lo audita y
encola la tarea de siempre. El correo lo entrega el worker por la API HTTPS de Brevo,
por el mismo camino que cualquier otra notificación. Si el envío vuelve a fallar, la
notificación vuelve a FALLIDO y el comando puede ejecutarse otra vez.

Igual que `promote_user`, la acción queda en `audit_logs` con `actor_type = system`: la
ejecuta el operador del sistema por consola y no hay usuario autenticado al que
atribuírsela.
"""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.notifications_app.models import Notification
from apps.notifications_app.services import dispatch


class Command(BaseCommand):
    help = "Reencola el envío de una notificación que quedó en FALLIDO."

    def add_arguments(self, parser):
        parser.add_argument(
            "notification_id",
            help="UUID de la notificación, que debe existir y estar en FALLIDO.",
        )

    def handle(self, *args, **options):
        notification_id = options["notification_id"]

        # El servicio no valida la forma del identificador: `filter(pk=...)` con un
        # valor mal formado levanta `ValidationError`. Traducirlo aquí convierte una
        # errata al copiar el UUID en un mensaje legible, no en una traza.
        try:
            notificacion = dispatch.resend(notification_id)
        except Notification.DoesNotExist as exc:
            raise CommandError(
                f"No existe ninguna notificación con el identificador «{notification_id}»."
            ) from exc
        except dispatch.ResendError as exc:
            raise CommandError(str(exc)) from exc
        except ValidationError as exc:
            raise CommandError(f"«{notification_id}» no es un UUID válido.") from exc

        self.stdout.write(
            self.style.SUCCESS(f"Notificación {notificacion.pk} devuelta a PENDIENTE y reencolada.")
        )
