# ADR-0007 — Almacenamiento privado de fotografías de reportes

**Estado:** Aceptada
**Sprint:** 6
**Fecha:** 2026-08-28

## Contexto

La creación de un reporte exige una fotografía, pero `ReportMedia.file_url` no debe convertirla
en un recurso público. La entrada proviene de un navegador y puede incluir metadata, una extensión
engañosa, contenido inválido o bytes ya usados. La base de datos y Storage tampoco comparten una
transacción distribuida: el orden de las operaciones y su compensación deben ser explícitos.

El proyecto ya usa Supabase Storage mediante un cliente HTTP propio del backend. Incorporar otro
proveedor o un SDK solo para reportes duplicaría infraestructura y credenciales.

## Decisión

### 1. Bucket privado y claves controladas por el servidor

Las fotografías viven en el bucket privado `reportes` de Supabase Storage. El backend genera la
clave `reports/{report_id}/{media_id}.{ext}` con UUID propios. El nombre enviado por el navegador
solo participa en la validación de extensión; nunca forma una ruta.

`ReportMedia.file_url` conserva su nombre legado, pero guarda esa clave de objeto, no una URL
pública o firmada. La API de creación no devuelve la clave ni una URL. Los medios no se sirven
directamente al público.

### 2. JPEG/PNG, 5 MiB y normalización sin metadata

Se aceptan únicamente JPEG y PNG de hasta 5 MiB. Se comprueban tamaño real del stream, extensión,
MIME declarado y formato que Pillow puede abrir y verificar. Extensión, MIME y contenido deben
coincidir.

El backend corrige orientación EXIF y vuelve a codificar píxeles limpios, sin copiar metadata. La
extensión y el MIME almacenados derivan del formato detectado. El límite se vuelve a comprobar
después de normalizar.

### 3. Hash original y unicidad parcial

`file_hash` es el SHA-256 de los bytes **originales**, calculado antes de normalizar. El constraint
`report_media_foto_hash_unico` lo hace único solo para `media_type = FOTO`. Una coincidencia previa
o concurrente responde `409`; la proximidad geográfica no participa en esta regla.

### 4. Upload antes de DB, seguido de compensación acotada

El backend prepara la foto, reserva UUID y confirma primero el upload. Luego abre una transacción
atómica para `Report`, `ReportMedia`, historial, recompensa y auditoría. Esta secuencia evita una
fila que apunte a un objeto que nunca existió.

Si la transacción falla —incluida una colisión concurrente del hash— se intenta eliminar únicamente
el objeto recién subido. El cleanup es _best-effort_: un fallo al borrar se registra sin clave
completa y nunca sustituye la excepción original. No existe rollback distribuido ni barrido masivo.

### 5. Credencial solo en el backend web

La credencial privilegiada de Storage y `SUPABASE_STORAGE_BUCKET_REPORTS` pertenecen al backend
web. No llegan al frontend ni son necesarias en el worker. Sin configuración o ante un fallo del
proveedor, la creación falla cerrada con `503` y no escribe filas parciales.

## Alternativas consideradas

| Alternativa                                     | Motivo de descarte                                                                 |
| ----------------------------------------------- | ---------------------------------------------------------------------------------- |
| Bucket público                                  | Expondría medios y haría imposible controlar autorización o retención              |
| Guardar una URL firmada                         | Expira; la base conservaría un valor muerto                                        |
| Guardar antes en DB                             | Puede dejar referencias permanentes a objetos ausentes                             |
| Hash de la copia normalizada                    | Cambia la definición de duplicado exacto y pierde evidencia de los bytes recibidos |
| Confiar solo en extensión o `Content-Type`      | Ambos son datos controlados por el cliente                                         |
| Retry automático del upload o del POST completo | Puede duplicar una mutación cuyo resultado es incierto                             |

## Consecuencias

- La foto no es pública y no conserva metadata del archivo original.
- Una foto idéntica se rechaza incluso bajo concurrencia.
- Puede quedar un objeto huérfano si falla también la compensación; el log permite investigarlo sin
  filtrar la clave completa.
- Consultar o entregar medios autorizados requerirá un contrato posterior; esta decisión no inventa
  URLs públicas ni políticas de retención.
