#!/usr/bin/env python3
"""
Control positivo del reclamo atómico.

La regla que lo motiva:

    Un registro append-only no puede servir de candado: bajo repetición, dos
    reclamos simultáneos "ganan" los dos. El registro anota; el motor excluye.

Y la regla de medición: *un negativo salido de una comprobación hecha a mano
necesita un control positivo antes de anotarse*. Así
que esta prueba primero **demuestra la carrera** con la forma vieja
(SELECT-luego-UPDATE) y sólo después afirma que la forma nueva la cierra. Si el
control positivo no enrojece, la prueba no está midiendo nada y lo dice.

    python3 pruebas/prueba-reclamo-atomico.py
"""
import os, sqlite3, sys, tempfile, threading, time

HILOS = 24

ESQUEMA = """
CREATE TABLE entries (genesis TEXT PRIMARY KEY, claimed_by TEXT, claim_expires TEXT);
INSERT INTO entries (genesis, claimed_by) VALUES ('at-prueba', NULL);
"""


def base():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "carrera.db")
    c = sqlite3.connect(p)
    c.executescript(ESQUEMA)
    c.commit()
    c.close()
    return p


def corre(path, reclamar):
    """Lanza HILOS reclamos simultáneos sobre la misma entrada; devuelve cuántos
    creyeron ganar."""
    ganadores = []
    lock = threading.Lock()
    listos = threading.Barrier(HILOS)

    def hilo(i):
        c = sqlite3.connect(path, timeout=10)
        listos.wait()                      # todos salen a la vez o no hay carrera
        try:
            if reclamar(c, f"monitor-{i}"):
                with lock:
                    ganadores.append(f"monitor-{i}")
        except sqlite3.OperationalError:
            pass                           # base ocupada cuenta como no-ganar
        finally:
            c.close()

    hs = [threading.Thread(target=hilo, args=(i,)) for i in range(HILOS)]
    [h.start() for h in hs]
    [h.join() for h in hs]
    return ganadores


def forma_vieja(c, quien):
    """SELECT y luego UPDATE: dos hilos leen NULL y los dos escriben."""
    fila = c.execute("SELECT claimed_by FROM entries WHERE genesis='at-prueba'").fetchone()
    if fila[0] is not None:
        return False
    time.sleep(0.002)                       # la ventana que existe igual, aquí visible
    c.execute("UPDATE entries SET claimed_by=? WHERE genesis='at-prueba'", (quien,))
    c.commit()
    return True


def forma_nueva(c, quien):
    """Un solo UPDATE condicional. Quien cambió una fila, ganó; el resto ve 0.
    El motor serializa la escritura: no hay ventana entre leer y escribir."""
    cur = c.execute(
        "UPDATE entries SET claimed_by=? WHERE genesis='at-prueba' AND claimed_by IS NULL",
        (quien,))
    c.commit()
    return cur.rowcount == 1


def main():
    print(f"{HILOS} hilos reclaman la misma entrada a la vez.\n")

    viejos = corre(base(), forma_vieja)
    print(f"  SELECT-luego-UPDATE   ganadores: {len(viejos)}  {viejos[:4]}{'…' if len(viejos) > 4 else ''}")
    if len(viejos) < 2:
        print("\n  El control positivo NO enrojeció: con esta máquina y este número de "
              "hilos\n  la forma vieja no llegó a colisionar, así que la prueba no "
              "demuestra nada.\n  Sube HILOS o el sleep antes de creerle al verde de abajo.")
        return 2

    nuevos = corre(base(), forma_nueva)
    print(f"  UPDATE condicional    ganadores: {len(nuevos)}  {nuevos}")
    print()
    if len(nuevos) != 1:
        print(f"  FALLA: la forma nueva dejó {len(nuevos)} ganadores, debía dejar 1.")
        return 1
    print(f"  OK · el control positivo enrojeció con {len(viejos)} ganadores y la forma")
    print("  nueva dejó exactamente 1. El reclamo excluye en el motor, no en el proceso.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
