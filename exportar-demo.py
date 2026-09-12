#!/usr/bin/env python3
"""
Arma el paquete desplegable en `dist/`.

En Vercel no corre un servidor persistente: corre una función por petición, con
el sistema de archivos de sólo lectura salvo `/tmp`. El servidor del registro ya es
un `BaseHTTPRequestHandler`, que es justo el patrón que el runtime de Python de
Vercel sabe invocar, así que **se despliega el servidor real** y no una
imitación: los filtros, los agregados y las mutaciones son los mismos.

Lo único que cambia es de dónde sale la base: la semilla viaja de sólo lectura
en el paquete y se copia a `/tmp` en el arranque en frío. Las escrituras
funcionan de verdad y se pierden cuando la instancia se recicla, que para una
demostración es exactamente lo que se quiere: siempre vuelve a un estado limpio.

    python3 exportar-demo.py && (cd dist && vercel deploy --prod)
"""
import os, shutil, sqlite3, subprocess, sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(RAIZ, "dist")
CRM = os.path.abspath(os.path.join(RAIZ, "..", "crm-demo"))

ENTRADA = '''"""Entrada de Vercel: el mismo servidor, con la base en /tmp."""
import os, shutil, sys, urllib.parse

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, RAIZ)

SEMILLA = os.path.join(RAIZ, "events.db")
VIVA = "/tmp/events.db"

# El arranque en frío copia la semilla. Cada instancia arranca de cero: las
# escrituras de un visitante no se mezclan con las de otro y la demostración
# nunca se degrada sola.
if not os.path.exists(VIVA):
    shutil.copyfile(SEMILLA, VIVA)

os.environ.setdefault("EVLOG_DEMO", "1")

import core
core.DB = VIVA

import server
server.WEB = os.path.join(RAIZ, "web")
server.H.db = core.connect(VIVA)

# La espera larga del feed no cabe en una función: bloquear 25 s consume el
# presupuesto de invocación para no traer nada. En la demostración el feed
# responde de inmediato y la interfaz sondea.
_feed = server.H.monitor_feed
def monitor_feed(self, c, key, q):
    q = dict(q, wait=["0"])
    return _feed(self, c, key, q)
server.H.monitor_feed = monitor_feed

# Con un rewrite, la función puede recibir la ruta reescrita en vez de la que
# pidió el navegador. Vercel conserva la original en un encabezado; si está, se
# restituye antes de despachar. Sin esto, toda la aplicación respondería el
# index a cualquier ruta y los errores serían silenciosos.
class Handler(server.H):
    def _ruta_real(self):
        """El rewrite entrega `/api/index` y la ruta que pidió el navegador viaja
        en `__ruta`, puesta por el propio rewrite. Adivinarla por encabezados no
        servía: ninguno de los tres que probé existe, y el sitio respondía 404 a
        todo. Se pasa explícita porque explícito se puede verificar."""
        if not self.path.startswith("/api/index"):
            return
        partes = self.path.split("?", 1)
        params = urllib.parse.parse_qsl(partes[1], keep_blank_values=True) if len(partes) > 1 else []
        ruta, resto = "/", []
        for k, v in params:
            if k == "__ruta":
                ruta = v or "/"
            else:
                resto.append((k, v))
        self.path = ruta + ("?" + urllib.parse.urlencode(resto) if resto else "")

    def do_GET(self):
        self._ruta_real()
        return server.H.do_GET(self)

    def do_POST(self):
        self._ruta_real()
        return server.H.do_POST(self)

    def do_PATCH(self):
        self._ruta_real()
        return server.H.do_PATCH(self)


handler = Handler
'''

VERCEL = '''{
  "functions": { "api/index.py": { "maxDuration": 15, "memory": 512 } },
  "rewrites": [{ "source": "/(.*)", "destination": "/api/index?__ruta=/$1" }],
  "headers": [
    { "source": "/(.*)",
      "headers": [
        { "key": "X-Robots-Tag", "value": "noindex, nofollow" },
        { "key": "Referrer-Policy", "value": "no-referrer" }
      ] }
  ]
}
'''


def limpiar_base(origen, destino):
    """Copia la base y borra lo que no debe viajar: los hashes de token de los
    monitores y de las fuentes. Un hash no es un secreto, pero tampoco tiene
    nada que hacer en un paquete público."""
    shutil.copyfile(origen, destino)
    c = sqlite3.connect(destino)
    c.execute("PRAGMA journal_mode=DELETE")      # sin WAL: un solo archivo viaja
    c.execute("UPDATE monitors SET token_hash=NULL")
    c.execute("UPDATE sources SET secret_hash=NULL")
    c.execute("DELETE FROM raw_payloads WHERE source='intake-publico'")
    c.commit()
    c.isolation_level = None          # VACUUM no corre dentro de una transacción
    c.execute("VACUUM")
    n = c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    s = c.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    c.close()
    return n, s


def main():
    # El enlace del proyecto vive dentro de dist/. Borrar el directorio entero lo
    # borraba también, y el despliegue siguiente creaba un proyecto nuevo llamado
    # como la carpeta ("dist"). Se conserva y se repone.
    enlace = os.path.join(DIST, ".vercel")
    guardado = None
    if os.path.isdir(enlace):
        guardado = os.path.join(RAIZ, ".vercel-guardado")
        if os.path.exists(guardado):
            shutil.rmtree(guardado)
        shutil.copytree(enlace, guardado)
    if os.path.exists(DIST):
        shutil.rmtree(DIST)
    os.makedirs(os.path.join(DIST, "api"))
    if guardado:
        shutil.copytree(guardado, os.path.join(DIST, ".vercel"))
        shutil.rmtree(guardado)

    for f in ("core.py", "server.py"):
        shutil.copyfile(os.path.join(RAIZ, f), os.path.join(DIST, f))
    shutil.copytree(os.path.join(RAIZ, "web"), os.path.join(DIST, "web"))
    shutil.copytree(os.path.join(RAIZ, "docs"), os.path.join(DIST, "docs"))

    with open(os.path.join(DIST, "api", "index.py"), "w", encoding="utf-8") as fh:
        fh.write(ENTRADA)
    with open(os.path.join(DIST, "vercel.json"), "w", encoding="utf-8") as fh:
        fh.write(VERCEL)
    open(os.path.join(DIST, "requirements.txt"), "w").close()
    with open(os.path.join(DIST, ".vercelignore"), "w", encoding="utf-8") as fh:
        fh.write("*.pyc\n__pycache__/\n")

    n, s = limpiar_base(os.path.join(RAIZ, "events.db"), os.path.join(DIST, "events.db"))
    tam = sum(os.path.getsize(os.path.join(d, f))
              for d, _, fs in os.walk(DIST) for f in fs) / 1e6
    print(f"dist/ listo · {n} entradas · {s} señales · {tam:.1f} MB")
    print(f"  desplegar:  cd {DIST} && vercel deploy --prod")


if __name__ == "__main__":
    main()
