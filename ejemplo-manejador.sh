#!/usr/bin/env bash
# Manejador de ejemplo. Recibe UN evento como JSON por stdin y decide qué hacer.
#
# Contrato:
#   stdin  -> {"seq","kind","at","genesis","actor","title","status","payload"}
#   entorno-> EVLOG_URL, EVLOG_TOKEN, EVLOG_MONITOR
#   salida 0 -> atendido, el cursor avanza
#   salida ≠0 -> NO atendido, el monitor se detiene y el evento se vuelve a entregar
set -euo pipefail
EV=$(cat)
KIND=$(printf '%s' "$EV" | python3 -c "import json,sys;print(json.load(sys.stdin)['kind'])")
GEN=$(printf '%s' "$EV" | python3 -c "import json,sys;print(json.load(sys.stdin).get('genesis') or '')")

case "$KIND" in
  entrada.creada)
    # Reaccionar: aquí iría avisar por Slack, abrir una rama, arrancar un agente…
    printf '  → reaccion: entrada nueva %s\n' "$GEN"
    ;;
  senal.ingerida)
    ACCION=$(printf '%s' "$EV" | python3 -c "import json,sys;print((json.load(sys.stdin).get('payload') or {}).get('action',''))")
    [ "$ACCION" = "complained" ] && printf '  → alerta: queja de spam, frenar volumen\n'
    ;;
esac
exit 0
