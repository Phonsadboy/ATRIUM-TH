#!/usr/bin/env bash
# Scheduled pg_dump for ATRIUM v2 (local disk + optional offsite copy).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATABASE_URL="${ATRIUM_DATABASE_URL:-postgresql://atrium:atrium@127.0.0.1:5432/atrium}"
BACKUP_DIR="${ATRIUM_BACKUP_DIR:-$ROOT/system/data/backups}"
OFFSITE_DIR="${ATRIUM_BACKUP_OFFSITE_DIR:-}"
REQUIRE_OFFSITE="${ATRIUM_BACKUP_REQUIRE_OFFSITE:-false}"
POSTGRES_CONTAINER="${ATRIUM_POSTGRES_CONTAINER:-atrium-postgres}"
POSTGRES_USER="${ATRIUM_POSTGRES_USER:-atrium}"
POSTGRES_DB="${ATRIUM_POSTGRES_DB:-atrium}"
BACKUP_SCHEMA="${ATRIUM_BACKUP_SCHEMA:-atrium}"

normalize_pg_url() {
  local url="$1"
  case "$url" in
    postgresql+asyncpg://*)
      printf 'postgresql://%s\n' "${url#postgresql+asyncpg://}"
      ;;
    *)
      printf '%s\n' "$url"
      ;;
  esac
}

docker_bin() {
  if [[ -n "${ATRIUM_DOCKER_BIN:-}" ]]; then
    printf '%s\n' "$ATRIUM_DOCKER_BIN"
  elif command -v docker >/dev/null 2>&1; then
    command -v docker
  elif [[ -x /usr/local/bin/docker ]]; then
    printf '%s\n' /usr/local/bin/docker
  elif [[ -x /opt/homebrew/bin/docker ]]; then
    printf '%s\n' /opt/homebrew/bin/docker
  else
    return 1
  fi
}

pgvector_available() {
  local pg_url="$1"
  local result=""
  if command -v psql >/dev/null 2>&1; then
    result="$(psql -Atq --dbname "$pg_url" -c "SELECT to_regtype('vector') IS NOT NULL" 2>/dev/null || true)"
    [[ "$result" == "t" || "$result" == "true" || "$result" == "1" ]]
    return
  fi

  local docker
  if docker="$(docker_bin)" && "$docker" info >/dev/null 2>&1; then
    result="$("$docker" exec "$POSTGRES_CONTAINER" psql -Atq -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT to_regtype('vector') IS NOT NULL" 2>/dev/null || true)"
    [[ "$result" == "t" || "$result" == "true" || "$result" == "1" ]]
    return
  fi
  return 1
}

include_vector_extension() {
  local pg_url="$1"
  case "${ATRIUM_BACKUP_INCLUDE_VECTOR_EXTENSION:-auto}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    0|false|FALSE|no|NO|off|OFF) return 1 ;;
  esac
  pgvector_available "$pg_url"
}

run_pg_dump() {
  local pg_url
  pg_url="$(normalize_pg_url "$DATABASE_URL")"
  local schema_args=()
  if [[ -n "$BACKUP_SCHEMA" && "$BACKUP_SCHEMA" != "all" ]]; then
    schema_args+=(--schema="$BACKUP_SCHEMA")
    # Schema-scoped dumps do not include extensions from public. Emit the
    # pgvector prelude only when the source database actually has pgvector.
    if include_vector_extension "$pg_url"; then
      printf 'CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;\n'
    fi
  fi
  if command -v pg_dump >/dev/null 2>&1; then
    pg_dump "${schema_args[@]}" "$pg_url"
    return
  fi

  local docker
  if ! docker="$(docker_bin)"; then
    echo "pg_dump not found and docker not found" >&2
    return 1
  fi
  if [[ -z "${DOCKER_CONFIG:-}" ]]; then
    export DOCKER_CONFIG="${ATRIUM_DOCKER_CONFIG:-/tmp/atrium-docker-config}"
  fi
  mkdir -p "$DOCKER_CONFIG"
  if [[ ! -f "$DOCKER_CONFIG/config.json" ]]; then
    printf '{}\n' > "$DOCKER_CONFIG/config.json"
  fi
  "$docker" exec "$POSTGRES_CONTAINER" pg_dump "${schema_args[@]}" -U "$POSTGRES_USER" -d "$POSTGRES_DB"
}

truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

hash_file() {
  shasum -a 256 "$1" | awk '{print $1}'
}

file_size() {
  wc -c < "$1" | tr -d '[:space:]'
}

write_manifest() {
  local copied="$1"
  local copied_to="$2"
  {
    printf 'format=atrium-backup-manifest-v1\n'
    printf 'created_utc=%s\n' "$STAMP"
    printf 'backup_file=%s\n' "$(basename "$OUT")"
    printf 'schema=%s\n' "$BACKUP_SCHEMA"
    printf 'postgres_container=%s\n' "$POSTGRES_CONTAINER"
    printf 'postgres_database=%s\n' "$POSTGRES_DB"
    printf 'sha256=%s\n' "$SHA"
    printf 'size_bytes=%s\n' "$SIZE_BYTES"
    printf 'gzip_ok=true\n'
    printf 'offsite_copied=%s\n' "$copied"
    printf 'offsite_dir=%s\n' "$copied_to"
  } > "$MANIFEST"
}

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/atrium-${STAMP}.sql.gz"
SHA_FILE="$OUT.sha256"
MANIFEST="$OUT.manifest"

echo "Backing up to $OUT"
echo "Schema: $BACKUP_SCHEMA"
run_pg_dump | gzip -9 > "$OUT"
gzip -t "$OUT"
SHA="$(hash_file "$OUT")"
SIZE_BYTES="$(file_size "$OUT")"
printf '%s  %s\n' "$SHA" "$(basename "$OUT")" > "$SHA_FILE"
write_manifest "false" ""

if [[ -z "$OFFSITE_DIR" ]] && truthy "$REQUIRE_OFFSITE"; then
  echo "ATRIUM_BACKUP_REQUIRE_OFFSITE is true but ATRIUM_BACKUP_OFFSITE_DIR is empty" >&2
  exit 1
fi

if [[ -n "$OFFSITE_DIR" ]]; then
  mkdir -p "$OFFSITE_DIR"
  cp -p "$OUT" "$SHA_FILE" "$OFFSITE_DIR/"
  OFFSITE_OUT="$OFFSITE_DIR/$(basename "$OUT")"
  OFFSITE_SHA="$(hash_file "$OFFSITE_OUT")"
  if [[ "$OFFSITE_SHA" != "$SHA" ]]; then
    echo "Offsite copy checksum mismatch: $OFFSITE_OUT" >&2
    exit 1
  fi
  write_manifest "true" "$OFFSITE_DIR"
  cp -p "$MANIFEST" "$OFFSITE_DIR/"
  echo "Copied to offsite: $OFFSITE_DIR"
fi

echo "SHA256: $SHA"
echo "Manifest: $MANIFEST"
echo "Done."
