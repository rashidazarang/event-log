#!/usr/bin/env bash
# =====================================================================
# Event Log · monitor de referencia
#
# Implementa el contrato completo en bash: inscribir, observar por cursor,
# reaccionar con un manejador propio y actuar (reclamar, avanzar, cerrar).
# Cualquier arnés puede reemplazarlo; esto documenta el protocolo ejecutándolo.
#
#   ./monitor.sh registrar mi-monitor --space sonda --event 'entrada.*' --actuar
#   EVLOG_TOKEN=... ./monitor.sh escuchar mi-monitor --manejador ./ejemplo-manejador.sh
#   EVLOG_TOKEN=... ./monitor.sh reclamar mi-monitor ev-20260910-abc123
#
# Sin dependencias más allá de curl y python3.
# =====================================================================
set -uo pipefail

URL="${EVLOG_URL:-http://127.0.0.1:8124}"
TOKEN="${EVLOG_TOKEN:-}"
ESTADO="${EVLOG_ESTADO:-$HOME/.evlog}"

c_dim=$'\033[2m'; c_cy=$'\033[36m'; c_gr=$'\033[32m'; c_am=$'\033[33m'
c_ro=$'\033[31m'; c_bo=$'\033[1m'; c_off=$'\033[0m'

morir() { printf '%s\n' "${c_ro}error:${c_off} $*" >&2; exit 1; }

# Lee un campo de un JSON en stdin sin depender de jq.
campo() { python3 -c "
import json,sys
d = json.load(sys.stdin)
for k in '$1'.split('.'):
    if isinstance(d, list):
        d = d[int(k)]
    else:
        d = (d or {}).get(k)
    if d is None: break
print('' if d is None else (d if isinstance(d,str) else json.dumps(d, ensure_ascii=False)))"; }

pedir() {  # método ruta [cuerpo]
  local m="$1" ruta="$2" cuerpo="${3:-}"
  local -a args=(-sS -X "$m" "$URL$ruta" -H 'Content-Type: application/json')
  [ -n "$TOKEN" ] && args+=(-H "Authorization: Bearer $TOKEN")
  [ -n "$cuerpo" ] && args+=(-d "$cuerpo")
  curl "${args[@]}"
}

# --------------------------------------------------------------- registrar --
cmd_registrar() {
  local clave="${1:?falta la clave del monitor}"; shift
  local nombre="$clave" tipo="shell" lease=900 desde="ahora" caps='["observar"]'
  local espacios=() eventos=() etiquetas=() estados=() abiertos=false
  while [ $# -gt 0 ]; do
    case "$1" in
      --nombre)    nombre="$2"; shift 2;;
      --tipo)      tipo="$2"; shift 2;;
      --space)     espacios+=("$2"); shift 2;;
      --event)     eventos+=("$2"); shift 2;;
      --label)     etiquetas+=("$2"); shift 2;;
      --status)    estados+=("$2"); shift 2;;
      --abiertos)  abiertos=true; shift;;
      --lease)     lease="$2"; shift 2;;
      --historial) desde="0"; shift;;
      --reaccionar) caps='["observar","reaccionar"]'; shift;;
      --actuar)    caps='["observar","reaccionar","actuar"]'; shift;;
      *) morir "opción desconocida: $1";;
    esac
  done
  local cuerpo
  cuerpo=$(EVLOG_C="$clave" EVLOG_N="$nombre" EVLOG_T="$tipo" EVLOG_L="$lease" \
           EVLOG_D="$desde" EVLOG_CAPS="$caps" EVLOG_AB="$abiertos" \
           ESP="${espacios[*]-}" EVT="${eventos[*]-}" LAB="${etiquetas[*]-}" EST="${estados[*]-}" \
    python3 -c "
import json, os
f = {}
for var, clave in (('ESP','space'), ('EVT','event'), ('LAB','labels_any'), ('EST','status')):
    v = os.environ.get(var, '').split()
    if v: f[clave] = v
if os.environ['EVLOG_AB'] == 'true': f['abiertos'] = True
print(json.dumps({'key': os.environ['EVLOG_C'], 'name': os.environ['EVLOG_N'],
                  'kind': os.environ['EVLOG_T'], 'filter': f,
                  'capabilities': json.loads(os.environ['EVLOG_CAPS']),
                  'lease_seconds': int(os.environ['EVLOG_L']),
                  'desde': os.environ['EVLOG_D']}, ensure_ascii=False))")
  local r; r=$(pedir POST /api/monitors "$cuerpo")
  local err; err=$(printf '%s' "$r" | campo error)
  [ -n "$err" ] && { printf '%s\n' "$r"; morir "$err"; }
  local tok; tok=$(printf '%s' "$r" | campo token)
  mkdir -p "$ESTADO" && umask 077 && printf '%s\n' "$tok" > "$ESTADO/$clave.token"
  printf '%s\n' "${c_gr}inscrito${c_off} ${c_bo}$clave${c_off}"
  printf '  filtro       %s\n' "$(printf '%s' "$r" | campo filtro)"
  printf '  capacidades  %s\n' "$(printf '%s' "$r" | campo capacidades)"
  printf '  cursor       %s de %s\n' "$(printf '%s' "$r" | campo cursor)" "$(printf '%s' "$r" | campo head)"
  printf '  token        %s\n' "${c_dim}guardado en $ESTADO/$clave.token${c_off}"
  printf '\n  %s\n' "${c_dim}EVLOG_TOKEN=\$(cat $ESTADO/$clave.token) $0 escuchar $clave${c_off}"
}

cargar_token() {
  local clave="$1"
  [ -n "$TOKEN" ] && return 0
  [ -f "$ESTADO/$clave.token" ] && TOKEN=$(cat "$ESTADO/$clave.token")
  [ -n "$TOKEN" ] || morir "sin token: exporta EVLOG_TOKEN o registra el monitor primero"
}

# ---------------------------------------------------------------- escuchar --
# El ciclo: pedir desde el cursor -> procesar -> confirmar (ack). El cursor NO
# avanza si el manejador falla, así que un evento nunca se da por visto sin
# haberse atendido. Es la diferencia entre una cola y una notificación.
cmd_escuchar() {
  local clave="${1:?falta la clave del monitor}"; shift
  local espera=25 limite=50 manejador="" una_vez=false silencio=false
  while [ $# -gt 0 ]; do
    case "$1" in
      --espera)    espera="$2"; shift 2;;
      --limite)    limite="$2"; shift 2;;
      --manejador) manejador="$2"; shift 2;;
      --una-vez)   una_vez=true; shift;;
      --silencio)  silencio=true; shift;;
      *) morir "opción desconocida: $1";;
    esac
  done
  cargar_token "$clave"
  [ -n "$manejador" ] && [ ! -x "$manejador" ] && morir "el manejador no es ejecutable: $manejador"

  printf '%s\n' "${c_cy}evlog${c_off} escuchando ${c_bo}$clave${c_off} en $URL ${c_dim}(ctrl-c para salir)${c_off}"
  local vueltas=0
  while :; do
    local r; r=$(pedir GET "/api/monitors/$clave/feed?wait=$espera&limit=$limite")
    local err; err=$(printf '%s' "$r" | campo error)
    if [ -n "$err" ]; then
      printf '%s %s\n' "${c_ro}feed${c_off}" "$err" >&2
      sleep 5; continue
    fi
    local n; n=$(printf '%s' "$r" | python3 -c "import json,sys;print(len(json.load(sys.stdin)['events']))")
    if [ "$n" -gt 0 ]; then
      # cada evento en una línea compacta y su JSON completo en la siguiente
      printf '%s' "$r" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for e in d['events']:
    ent = e.get('entrada') or {}
    print(json.dumps({'seq': e['seq'], 'kind': e['kind'], 'at': e['at'],
                      'genesis': e.get('genesis'), 'actor': e.get('actor'),
                      'title': ent.get('title'), 'status': ent.get('status'),
                      'payload': e.get('payload')}, ensure_ascii=False))" |
      while IFS= read -r linea; do
        if [ "$silencio" = false ]; then
          printf '%s' "$linea" | python3 -c "
import json,sys
e = json.load(sys.stdin)
C = {'creada':'\033[36m','movida':'\033[35m','cerrada':'\033[32m','reclamada':'\033[33m',
     'ingerida':'\033[2m','vencido':'\033[31m'}
c = C.get(e['kind'].split('.')[-1], '')
print(f\"{'\033[2m'}{e['seq']:>6}{'\033[0m'} {c}{e['kind']:<20}{'\033[0m'} \"
      f\"{(e.get('title') or json.dumps(e.get('payload'), ensure_ascii=False))[:78]}\")"
        fi
        if [ -n "$manejador" ]; then
          if ! printf '%s' "$linea" | EVLOG_URL="$URL" EVLOG_TOKEN="$TOKEN" \
               EVLOG_MONITOR="$clave" "$manejador"; then
            printf '%s manejador falló; el cursor no avanza\n' "${c_am}aviso:${c_off}" >&2
            break 2
          fi
        fi
      done
      local sig; sig=$(printf '%s' "$r" | campo siguiente)
      pedir POST "/api/monitors/$clave/ack" "{\"cursor\": $sig}" > /dev/null
    fi
    vueltas=$((vueltas + 1))
    [ "$una_vez" = true ] && break
    [ "$n" -eq 0 ] && sleep 1
  done
}

# ------------------------------------------------------------------ actuar --
cmd_reclamar() { local k="${1:?}" g="${2:?}"; cargar_token "$k"
  pedir POST "/api/monitors/$k/claim" "{\"genesis\":\"$g\",\"nota\":\"${3:-}\"}" | py_pretty; }
cmd_avance()   { local k="${1:?}" g="${2:?}"; cargar_token "$k"
  pedir POST "/api/monitors/$k/progress" "{\"genesis\":\"$g\",\"nota\":\"${3:-}\"}" | py_pretty; }
cmd_cerrar()   { local k="${1:?}" g="${2:?}"; cargar_token "$k"
  pedir POST "/api/monitors/$k/complete" \
    "{\"genesis\":\"$g\",\"status\":\"${3:-en revision}\",\"nota\":\"${4:-}\"}" | py_pretty; }
cmd_liberar()  { local k="${1:?}" g="${2:?}"; cargar_token "$k"
  pedir POST "/api/monitors/$k/release" "{\"genesis\":\"$g\",\"motivo\":\"${3:-}\"}" | py_pretty; }
cmd_estado()   { local k="${1:?}"; cargar_token "$k"
  pedir GET "/api/monitors/$k/feed?limit=0&wait=0" | python3 -c "
import json,sys
d = json.load(sys.stdin)
print(f\"monitor    {d.get('monitor')}\")
print(f\"cursor     {d.get('cursor')} de {d.get('head')}  (atraso {d.get('atraso')})\")
print(f\"capacidades{'':1}{d.get('capacidades')}\")
print(f\"filtro     {json.dumps(d.get('filtro'), ensure_ascii=False)}\")"; }

# La salida se imprime completa y válida: truncarla la hacía impipeable, que es
# justo lo que uno quiere de un CLI de bus (`... | jq .hasta`).
py_pretty() { python3 -c "import json,sys;print(json.dumps(json.load(sys.stdin),ensure_ascii=False,indent=2))"; }

uso() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

case "${1:-}" in
  registrar) shift; cmd_registrar "$@";;
  escuchar)  shift; cmd_escuchar "$@";;
  reclamar)  shift; cmd_reclamar "$@";;
  avance)    shift; cmd_avance "$@";;
  cerrar)    shift; cmd_cerrar "$@";;
  liberar)   shift; cmd_liberar "$@";;
  estado)    shift; cmd_estado "$@";;
  *) uso;;
esac
