#!/usr/bin/env bash
# Ejecuta verificaciones en los recursos Docker sintéticos exclusivos de S15.
set -euo pipefail

S15_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
S15_OUTPUT=${S15_LOCAL_OUTPUT:-"${XDG_DATA_HOME:-$HOME/.local/share}/ciudadania-activa/s15/20261020/out"}
S15_IMAGE=${S15_TEST_IMAGE:-ca-s12-backend-test:dev}
S15_SETTINGS=${S15_LOCAL_SETTINGS:-config.settings.test}

if [[ $# -eq 0 ]]; then
  printf '%s\n' 'Uso: tools/s15/check_local.sh <comando> [argumentos...]' >&2
  exit 2
fi
if [[ $S15_SETTINGS != config.settings.test && $S15_SETTINGS != config.settings.dev ]]; then
  printf '%s\n' 'Solo se admiten los ajustes locales dev/test.' >&2
  exit 2
fi
if [[ $S15_OUTPUT != /* || $S15_OUTPUT == /tmp/* || $S15_OUTPUT == "$S15_ROOT"* ]]; then
  printf '%s\n' 'La salida debe ser absoluta, duradera y externa al repositorio y /tmp.' >&2
  exit 2
fi
for S15_SERVICE in ca-s15-db ca-s15-redis; do
  S15_LABEL=$(docker inspect --format '{{ index .Config.Labels "ciudadania.sprint" }}' "$S15_SERVICE")
  if [[ $S15_LABEL != 15 ]]; then
    printf '%s\n' 'Los recursos deben llevar la etiqueta ciudadania.sprint=15.' >&2
    exit 2
  fi
done
S15_INTERNAL=$(docker network inspect --format '{{.Internal}}' ca-s15-local)
if [[ $S15_INTERNAL != true ]]; then
  printf '%s\n' 'ca-s15-local debe ser una red interna sin salida a proveedores.' >&2
  exit 2
fi

mkdir -p -- "$S15_OUTPUT/s15" "$S15_OUTPUT/home"
chmod 700 -- "$S15_OUTPUT" "$S15_OUTPUT/s15" "$S15_OUTPUT/home"
S15_MOUNTS=(--mount "type=bind,src=$S15_ROOT,dst=/work,readonly")
if [[ -f $S15_ROOT/.env ]]; then
  S15_MOUNTS+=(--mount type=bind,src=/dev/null,dst=/work/.env,readonly)
fi

exec docker run --rm --network ca-s15-local --user "$(id -u):$(id -g)" \
  --label ciudadania.sprint=15 \
  --tmpfs /work/backend/.s11-test-artifacts:rw,mode=1777 \
  "${S15_MOUNTS[@]}" --mount "type=bind,src=$S15_OUTPUT,dst=/out" \
  -w /work/backend \
  -e HOME=/out/home -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/work:/work/backend \
  -e COVERAGE_FILE=/out/.coverage-s15 -e RUFF_CACHE_DIR=/out/ruff-cache \
  -e "DJANGO_SETTINGS_MODULE=$S15_SETTINGS" \
  -e DJANGO_SECRET_KEY=s15-synthetic-local-test-key-not-production \
  -e DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,testserver \
  -e DATABASE_URL=postgis://ciudadania:s15-local-database-only@ca-s15-db:5432/ciudadania_s15 \
  -e REDIS_URL=redis://ca-s15-redis:6379/0 \
  -e AI_CLASSIFICATION_ENABLED=false -e AI_PROVIDER= \
  -e OPENAI_API_KEY= -e GEMINI_API_KEY= -e BREVO_API_KEY= \
  -e S11_EXPERIMENTS_ENABLED=false \
  -e SUPABASE_URL=http://127.0.0.1:18515 \
  -e SUPABASE_SERVICE_ROLE_KEY=s15-synthetic-local-storage-only \
  -e SUPABASE_STORAGE_BUCKET_PRIVATE=s15-private \
  -e SUPABASE_STORAGE_BUCKET_REPORTS=s15-reports \
  -e DOCUMENT_HMAC_SECRET=s15-synthetic-local-document-key-not-production \
  "$S15_IMAGE" "$@"
