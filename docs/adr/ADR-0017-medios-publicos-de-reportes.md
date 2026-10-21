# ADR-0017: fotografías y evidencia pública mediante proxies revocables

- **Estado:** aceptada, 20/10/2026, Fase B de S15-UI.
- **Decisiones vinculantes:** `00_DECISIONES.md`, sección «Fase B — respuestas del usuario».
- **Sustituye parcialmente:** ADR-0007, restricción de lectura pública de fotografías
  de reportes; aclara la misma restricción histórica de ADR-0003. El almacenamiento
  privado y la privacidad de identidad de ADR-0006 siguen vigentes.

## Contexto

La transparencia del mapa necesita la fotografía validada y la evidencia de resolución.
El bucket continúa privado. Publicar URLs firmadas impediría retirar inmediatamente el
acceso al rechazar un reporte. Quitar EXIF no anonimiza rostros, patentes ni texto visible.

El usuario confirmó que las fotografías existentes son sintéticas o de prueba: **no se
requiere tratamiento retroactivo**. Esta decisión no autoriza publicar selfies ni
imágenes de documentos de identidad, que nunca pasan por estas vistas.

## Decisión

- La primera fotografía `FOTO`, ordenada por `created_at, id`, es pública únicamente en
  `ASIGNADO`, `EN_PROCESO`, `RESUELTO` y `CERRADO`.
- Toda evidencia de resolución del reporte es pública únicamente en `RESUELTO` y
  `CERRADO`, incluidos sus ciclos anteriores. No se publican descripción libre,
  operador, hashes, claves de idempotencia ni claves de almacenamiento.
- Listado y detalle exponen `photo_url: string | null`. Solo el detalle agrega
  `resolution_evidences: [{id, evidence_url, created_at}]`; sin evidencia habilitada, `[]`.
- Los enlaces son rutas relativas a la raíz de la **API**, estables y sin tokens:
  `GET /api/v1/public/reports/{report_id}/photo/` y
  `GET /api/v1/public/reports/{report_id}/evidence/{evidence_id}/`.
- Cada descarga vuelve a consultar el estado y la relación con el reporte. La única
  clave utilizada es la guardada en la base; ningún parámetro del cliente elige una
  clave o un bucket. No se reutilizan los tokens ni el endpoint privado de evidencia.
- Respuesta JPEG/PNG, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`.
  Reporte inexistente, estado no habilitado, medio ausente o ajeno y objeto inexistente
  en Storage tienen el mismo 404: `{"detail":"No se encontró la imagen."}`.
  Una indisponibilidad de Storage devuelve 503 genérico, sin información interna.
- Acceso anónimo (`authentication_classes=[]`, `AllowAny`) y scope propio por IP
  `public_report_media`, 120/min compartido por fotos y evidencia, independiente del mapa.
- El rechazo institucional o administrativo retira ambos medios de los proxies. La
  caché de listados puede conservar un enlace durante su TTL (60 s); el enlace ya
  devuelve 404. No es posible retirar copias que alguien haya descargado previamente.
- La normalización existente reconstruye los píxeles y elimina EXIF/GPS. Se agrega
  prueba explícita con un IFD GPS real, para JPEG y PNG. La evidencia usa esa misma
  normalización en su carga.
- La clave generacional del listado pasa a `public-reports:v2`; no hay migraciones
  ni modificaciones de los contratos privados.

## Consecuencias e integración

La Fase 5 agregará el aviso aprobado al paso de foto:
«La foto será pública: evitá caras, patentes y datos personales.» También implementará
carga diferida de imágenes y los parsers y listas blancas nuevos. B1 modifica solo
backend y documentación. **No desplegar este contrato por separado contra el frontend
anterior:** sus parsers exactos rechazan campos adicionales. Coordinar la activación
con Fase 5 y ejecutar las pruebas de integración antes del despliegue.

La advertencia y la revisión institucional reducen exposición, pero no constituyen un
servicio automático de anonimización visual. El retiro opera sobre futuras descargas,
no sobre copias externas.
