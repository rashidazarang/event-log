#!/usr/bin/env python3
"""
Event Log · monitor reactivo — triage automático del trabajo entrante.

Observa el log, decide con `reglas.json` y actúa: reclama, comenta con la regla
que disparó, asigna y suelta. Es un proceso externo: habla sólo por el
protocolo público, igual que lo haría un arnés de otro.

Prácticas de instrumentación que este repositorio aplica:

- **Decide desde el estado actual, no desde el evento.** Un evento reentregado
  tras una caída vuelve a leer la entrada y concluye lo mismo. La idempotencia
  no se pide, se construye.
- **Bitácora append-only, una línea por decisión**, con la versión de reglas que
  la produjo. Una corrección es una línea nueva, nunca una reescritura.
- **El bucket de descartes se abre siempre.** Lo que el monitor decidió NO hacer
  se anota con su razón. Un triage que sólo registra sus aciertos no se puede
  auditar: la pregunta interesante es qué dejó pasar.
- **La identidad se anuncia.** Sin actor determinable, se dice en voz alta en vez
  de fingir orden.

    python3 monitor-reactivo.py --registrar
    python3 monitor-reactivo.py                 # cicla hasta ~/.evlog/stop-reactivo
    python3 monitor-reactivo.py --vueltas 1     # una vuelta y sale
"""
import argparse, json, os, sys, time, urllib.error, urllib.request
import datetime as dt

RAIZ = os.path.dirname(os.path.abspath(__file__))
URL = os.environ.get("EVLOG_URL", "http://127.0.0.1:8124")
ESTADO = os.environ.get("EVLOG_ESTADO", os.path.expanduser("~/.evlog"))
CLAVE = "reactivo"
BITACORA = os.path.join(RAIZ, "evidencia", "bitacora-reactiva.jsonl")
PARAR = os.path.join(ESTADO, "stop-reactivo")

FILTRO = {"event": ["entrada.creada", "entrada.movida", "senal.ingerida"]}
CAPACIDADES = ["observar", "reaccionar", "actuar"]


def ahora():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def reglas():
    with open(os.path.join(RAIZ, "reglas.json"), encoding="utf-8") as fh:
        return json.load(fh)


def llamar(metodo, ruta, cuerpo=None, token=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        URL + ruta, method=metodo, headers=h,
        data=json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {"error": e.reason}
    except urllib.error.URLError as e:
        return 0, {"error": str(e.reason)}


def anota(fila):
    """Append-only. Una decisión escrita no se reescribe nunca."""
    os.makedirs(os.path.dirname(BITACORA), exist_ok=True)
    with open(BITACORA, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(fila, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())          # la evidencia sobrevive a un corte


# ------------------------------------------------------------------ decisión --
def destino(entrada, R):
    """Devuelve (responsable, regla) o (None, None). El orden lo fija el archivo,
    no el código: cambiarlo es un cambio de reglas, con su versión."""
    e = R["enrutamiento"]
    for criterio in e["orden"]:
        if criterio == "etiqueta":
            for l in entrada.get("labels") or []:
                if l in e["por_etiqueta"]:
                    return e["por_etiqueta"][l], f"etiqueta:{l}"
        elif criterio == "prioridad":
            p = entrada.get("priority")
            if p in e["por_prioridad"]:
                return e["por_prioridad"][p], f"prioridad:{p}"
        elif criterio == "espacio":
            sp = entrada.get("space")
            if sp in e["por_espacio"]:
                return e["por_espacio"][sp], f"espacio:{sp}"
    return e.get("sin_regla"), None


def decidir(ev, entrada, R):
    """(accion, razon, extra). Se lee la ENTRADA, no la carga del evento."""
    if ev["kind"] == "senal.ingerida":
        p = ev.get("payload") or {}
        if p.get("action") != "complained":
            return "descartar", f"señal {p.get('action')} sin regla reactiva", None
        # Una señal no es una entrada: no se puede reclamar. Se escala la entrada
        # que la fuente abrió para ella, y si la fuente no abrió ninguna eso se
        # dice — crear trabajo es del bucle proactivo, no de aquí.
        if not entrada:
            return "descartar", (f"queja en {p.get('dominio')} sin entrada asociada "
                                 f"(sujeto {p.get('subject_id')})"), None
        return "alerta", f"queja de spam en {p.get('dominio') or 'dominio desconocido'}", p

    if not entrada:
        return "descartar", "el evento no trae entrada legible", None
    if entrada["status"] in ("cerrado", "descartado"):
        return "descartar", f"ya está {entrada['status']}", None
    if entrada.get("claimed_by"):
        return "descartar", f"reclamada por {entrada['claimed_by']}", None

    if ev["kind"] == "entrada.movida" and entrada["status"] == "bloqueado" \
            and not entrada.get("blocked_by"):
        return "preguntar", "quedó bloqueada sin decir a quién se espera", None

    if entrada.get("assignee"):
        return "descartar", f"ya tiene dueño ({entrada['assignee']})", None

    quien, regla = destino(entrada, R)
    if not quien:
        return "descartar", "ninguna regla de enrutamiento aplica", None
    return "asignar", regla, quien


# -------------------------------------------------------------------- acción --
class Reactivo:
    def __init__(self, token, verbose=True):
        self.token = token
        self.verbose = verbose
        self.R = reglas()
        self.stats = {"vistos": 0, "asignados": 0, "alertas": 0, "preguntas": 0,
                      "descartados": 0, "fallos": 0}

    def log(self, *a):
        if self.verbose:
            print(*a, flush=True)

    def act(self, ruta, cuerpo):
        return llamar("POST", f"/api/monitors/{CLAVE}/{ruta}", cuerpo, self.token)

    def atender(self, ev):
        self.stats["vistos"] += 1
        g = ev.get("genesis")
        entrada = None
        if g:
            code, entrada = llamar("GET", f"/api/entries/{g}")
            if code != 200:
                entrada = None
        elif ev["kind"] == "senal.ingerida" and (ev.get("payload") or {}).get("action") == "complained":
            sujeto = (ev["payload"] or {}).get("subject_id") or ""
            code, res = llamar("GET", f"/api/entries?text={sujeto}&abiertos=1&per=1")
            if code == 200 and res.get("items"):
                entrada = res["items"][0]
                g = entrada["genesis"]
        accion, razon, extra = decidir(ev, entrada, self.R)
        fila = {"at": ahora(), "seq": ev["seq"], "evento": ev["kind"], "genesis": g,
                "reglas_version": self.R["version"], "decision": accion, "razon": razon}

        if accion == "descartar":
            self.stats["descartados"] += 1
            fila["resultado"] = "sin accion"
            anota(fila)                       # el bucket de descartes se abre siempre
            return True

        # reclamar antes de tocar: si otro monitor ya la tiene, esto da 409 y se anota
        code, r = self.act("claim", {"genesis": g, "lease": 120,
                                     "nota": f"triage reactivo · {razon}"})
        if code != 200:
            # 409 es un desenlace legítimo (otro lo tiene): atendido.
            # Cualquier otro código es infraestructura rota: hay que reintentar.
            legitimo = code == 409
            self.stats["descartados" if legitimo else "fallos"] += 1
            fila.update(resultado="no reclamada", http=code, detalle=r.get("error"),
                        decision="descartar" if legitimo else "reintentar",
                        razon=f"{razon} · pero {r.get('error')}")
            anota(fila)
            self.log(f"  {ev['seq']:>6} {ev['kind']:<18} no se pudo reclamar: {r.get('error')}")
            return legitimo

        try:
            if accion == "asignar":
                code, r = self.act("annotate", {
                    "genesis": g, "assignee": extra,
                    "labels_add": ["triage-auto"],
                    "regla": f"reactivo v{self.R['version']} · {razon}",
                    "comentario": f"Asignada a {extra} por la regla {razon} "
                                  f"(reglas v{self.R['version']}). Si el enrutamiento está mal, "
                                  f"corrige reglas.json y sube la versión: reasignar a mano deja "
                                  f"la regla equivocada viva para la siguiente."})
                self.stats["asignados" if code == 200 else "fallos"] += 1
                fila.update(destino=extra, resultado="asignada" if code == 200 else "falló",
                            http=code, detalle=r.get("error"))
                self.log(f"  {ev['seq']:>6} {ev['kind']:<18} → {extra:<16} [{razon}]")

            elif accion == "preguntar":
                code, r = self.act("annotate", {
                    "genesis": g, "labels_add": ["falta-bloqueador"],
                    "regla": f"reactivo v{self.R['version']}",
                    "comentario": "Quedó bloqueada sin decir a quién se espera. Un bloqueo sin "
                                  "destinatario no se puede perseguir: nadie sabe a quién "
                                  "recordarle. Llena `blocked_by`."})
                self.stats["preguntas" if code == 200 else "fallos"] += 1
                fila.update(resultado="comentada" if code == 200 else "falló", http=code)
                self.log(f"  {ev['seq']:>6} {ev['kind']:<18} ← falta bloqueador")

            elif accion == "alerta":
                dom = (extra or {}).get("dominio", "?")
                code, r = self.act("annotate", {
                    "genesis": g, "priority": "P0", "assignee": "dana",
                    "labels_add": ["reputacion", "urgente"],
                    "regla": f"reactivo v{self.R['version']} · queja",
                    "comentario": f"Una queja pesa mucho más que un rebote. Detener el "
                                  f"incremento de volumen en {dom} hasta revisar la lista."})
                self.stats["alertas" if code == 200 else "fallos"] += 1
                fila.update(resultado="escalada" if code == 200 else "falló", http=code,
                            dominio=dom)
                self.log(f"  {ev['seq']:>6} {ev['kind']:<18} ⚠ queja en {dom}")
        finally:
            self.act("release", {"genesis": g, "motivo": "triage terminado"})
        anota(fila)
        return fila.get("http") == 200

    def vuelta(self, espera=25, limite=50):
        """Pedir, procesar, confirmar SÓLO el prefijo atendido.

        Confirmar el lote entero cuando una acción falló da los eventos por
        vistos sin haberlos atendido, y se pierden en silencio: pasó aquí, con
        nueve asignaciones perdidas contra un endpoint que el servidor vivo
        todavía no tenía. `monitor.sh` ya lo hacía bien; esto no. Es el mismo
        principio que rige cualquier arnés de medición: una corrida fallida nunca avanza
        la línea base.
        """
        code, lote = llamar("GET", f"/api/monitors/{CLAVE}/feed?wait={espera}&limit={limite}",
                            token=self.token)
        if code != 200:
            self.log(f"  feed: {lote.get('error')}")
            time.sleep(5)
            return 0
        confirmable = None
        for ev in lote["events"]:
            if self.atender(ev):
                confirmable = ev["seq"]
            else:
                self.log(f"  ↩ el cursor se queda en {confirmable or 'donde estaba'}; "
                         f"seq {ev['seq']} se vuelve a entregar")
                break
        if confirmable is not None:
            llamar("POST", f"/api/monitors/{CLAVE}/ack", {"cursor": confirmable}, self.token)
        return len(lote["events"])


# ----------------------------------------------------------------- inscribir --
def registrar():
    code, r = llamar("POST", "/api/monitors", {
        "key": CLAVE, "name": "Triage reactivo", "owner": "dana", "kind": "agente",
        "filter": FILTRO, "capabilities": CAPACIDADES, "lease_seconds": 120,
        "meta": {"reglas": reglas()["version"], "arnes": "monitor-reactivo.py"}})
    if code != 201:
        sys.exit(f"no se pudo inscribir: {r.get('error')}")
    os.makedirs(ESTADO, exist_ok=True)
    ruta = os.path.join(ESTADO, f"{CLAVE}.token")
    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write(r["token"] + "\n")
    os.chmod(ruta, 0o600)
    print(f"inscrito {CLAVE} · cursor {r['cursor']} de {r['head']} · token en {ruta}")
    print(f"  filtro      {json.dumps(r['filtro'], ensure_ascii=False)}")
    print(f"  capacidades {r['capacidades']}")
    return r["token"]


def token_de_disco():
    ruta = os.path.join(ESTADO, f"{CLAVE}.token")
    if os.environ.get("EVLOG_TOKEN"):
        return os.environ["EVLOG_TOKEN"]
    if os.path.exists(ruta):
        return open(ruta, encoding="utf-8").read().strip()
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--registrar", action="store_true")
    ap.add_argument("--vueltas", type=int, default=0, help="0 = hasta el archivo de paro")
    ap.add_argument("--espera", type=int, default=25)
    ap.add_argument("--silencio", action="store_true")
    a = ap.parse_args()

    tok = registrar() if a.registrar else token_de_disco()
    if not tok:
        sys.exit("sin token: corre con --registrar o exporta EVLOG_TOKEN")
    if a.registrar and a.vueltas == 0 and not sys.stdout.isatty():
        return

    m = Reactivo(tok, verbose=not a.silencio)
    m.log(f"reactivo escuchando · reglas v{m.R['version']} · parar con: touch {PARAR}")
    v = 0
    try:
        while True:
            if os.path.exists(PARAR):
                m.log("archivo de paro presente; salgo")
                break
            m.vuelta(espera=a.espera)
            v += 1
            if a.vueltas and v >= a.vueltas:
                break
    except KeyboardInterrupt:
        pass
    m.log("\n  " + " · ".join(f"{k} {v}" for k, v in m.stats.items()))
    m.log(f"  bitácora: {BITACORA}")


if __name__ == "__main__":
    main()
