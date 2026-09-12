#!/usr/bin/env python3
"""
Resumen del ledger del bucle, en markdown. Calcado en espíritu de
`tools/arnes-sonda/evidence-summary.py`: el ledger es la evidencia y este
script sólo la lee; no corrige, no completa, no interpreta.

    python3 evidencia/resumen.py [evidencia/ledger.jsonl]
"""
import json, os, sys

RUTA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "ledger.jsonl")


def main():
    if not os.path.exists(RUTA):
        sys.exit(f"sin ledger en {RUTA}")
    filas = [json.loads(l) for l in open(RUTA, encoding="utf-8") if l.strip()]
    if not filas:
        sys.exit("ledger vacío")

    sondas = []
    for f in filas:
        for n in (f.get("lecturas") or {}):
            if n not in sondas:
                sondas.append(n)

    print(f"# Ledger del bucle · {len(filas)} corridas\n")
    print(f"Archivo: `{RUTA}`\n")

    enc = ["corrida", "instrumento", "reglas", "modo", "comp."] + sondas + ["abre", "act.", "cierra", "falsif."]
    print("| " + " | ".join(enc) + " |")
    print("|" + "|".join("---" for _ in enc) + "|")
    for f in filas:
        a = f.get("acciones") or {}
        comp = {True: "sí", False: "**no**", None: "null"}.get(f.get("comparable"), "?")
        cel = [f["at"][5:16].replace("T", " "),
               f["instrumento"]["version"], f.get("reglas_version", "?"),
               f.get("modo", "?"), comp]
        for n in sondas:
            l = (f.get("lecturas") or {}).get(n)
            if not l:
                cel.append("—")
            elif l["valor"] is None:
                cel.append("**null**")
            else:
                cel.append(str(l["valor"]))
        cel += [str(len(a.get("abiertas", []))), str(len(a.get("actualizadas", []))),
                str(len(a.get("cerradas", []))), str(len(f.get("falsificadas") or []))]
        print("| " + " | ".join(cel) + " |")

    # una lectura null no es un cero: se nombra aparte para que nadie la promedie
    nulos = [(f["at"], n) for f in filas for n, l in (f.get("lecturas") or {}).items()
             if l.get("valor") is None]
    if nulos:
        print("\n## Lecturas sin acceso\n")
        print("Una sonda que no pudo medir anota `null`. No es cero: con cero, un "
              "servidor caído se lee como salud perfecta.\n")
        for at, n in nulos:
            print(f"- `{at}` · **{n}**")

    derivas = [f for f in filas if f.get("deriva")]
    if derivas:
        print("\n## Deriva del instrumento\n")
        for f in derivas:
            d = f["deriva"]
            print(f"### {d['desde']} → {d['a']}  ·  `{f['at']}`\n")
            print(f"{d['razon']}\n")
            if d["movieron"]:
                print("| sonda | antes | ahora |")
                print("|---|---|---|")
                for n, m in d["movieron"].items():
                    print(f"| {n} | {m['antes']} | {m['ahora']} |")
                print("\nLas que se movieron deben ser exactamente las que el cambio predice. "
                      "Si se mueve otra, el instrumento movió más de lo que decía.\n")
            else:
                print("Ninguna lectura se movió.\n")

    fal = [(f["at"], f["instrumento"]["version"], x)
           for f in filas for x in (f.get("falsificadas") or [])]
    if fal:
        print("\n## Predicciones falsificadas\n")
        print("Una predicción falsificada es un resultado. Queda escrita; se corrige la "
              "predicción, no el umbral.\n")
        for at, v, x in fal:
            print(f"- `{at}` · instrumento v{v} · {x}")


if __name__ == "__main__":
    main()
