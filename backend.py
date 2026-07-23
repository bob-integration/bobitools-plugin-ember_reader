# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 BOBI SAS, France
# Auteur : Cyril Mazouer, pour le compte de BOBI SAS
# Distribué sous licence GNU GPL v3 (ou ultérieure) ; voir le fichier LICENSE.

"""Backend « Ember+ Reader » — inspecteur d'arbre Ember+ générique.

Se connecte à N'IMPORTE QUEL provider Ember+ (host:port), relève tout son arbre en
lecture seule (GetDirectory récursif), et le renvoie à plat pour l'UI. Outil de debug —
et brique de base pour un futur pilotage Ember+ (ex. Ross NEWT).

Contrat in-process : def api(path, method, payload, ctx) -> data | (status, data).
"""
import os
import time
import importlib.util

# Le loader d'outils charge backend.py par CHEMIN (pas comme un package), donc `from .` est
# indisponible : on charge le client Ember+ voisin par son chemin absolu. Le module reste
# séparé et réutilisable (brique de base pour un futur pilotage Ember+).
_spec = importlib.util.spec_from_file_location(
    "bt_ember_reader_client", os.path.join(os.path.dirname(__file__), "ember_client.py"))
_client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_client)
crawl = _client.crawl

# Cibles récemment interrogées, persistées dans le store scellé à l'outil (confort UI).
_TARGETS_SCOPE = "target"
_MAX_TARGETS = 20


def api(path, method, payload, ctx):
    parts = [p for p in (path or "").split("/") if p]
    payload = payload or {}

    if parts == ["crawl"] and method == "POST":
        return _crawl(payload, ctx)
    if parts == ["targets"] and method == "GET":
        return {"targets": _list_targets(ctx)}
    if parts == ["targets"] and method == "DELETE":
        for t in ctx.store.list(scope=_TARGETS_SCOPE):
            ctx.store.delete(t["id"])
        return {"ok": True}
    return 404, {"error": "route inconnue"}


def _crawl(payload, ctx):
    host = (str(payload.get("host") or "")).strip()
    if not host:
        return 400, {"error": "hôte requis"}
    try:
        port = int(payload.get("port") or 9000)
    except (TypeError, ValueError):
        return 400, {"error": "port invalide"}
    if not (1 <= port <= 65535):
        return 400, {"error": "port hors bornes"}
    try:
        timeout = float(payload.get("timeout") or 4.0)
    except (TypeError, ValueError):
        timeout = 4.0
    timeout = max(1.0, min(20.0, timeout))

    ctx.audit("crawl", "%s:%d" % (host, port))
    t0 = time.time()
    tree, err = crawl(host, port, timeout=timeout)
    elapsed = round((time.time() - t0) * 1000)
    if err:
        return {"ok": False, "error": err, "host": host, "port": port, "elapsed_ms": elapsed}

    _remember_target(ctx, host, port)
    nodes = sum(1 for e in tree.values() if e["kind"] == "node")
    params = sum(1 for e in tree.values() if e["kind"] == "param")
    return {"ok": True, "host": host, "port": port, "elapsed_ms": elapsed,
            "count": len(tree), "nodes": nodes, "params": params, "tree": tree}


def _list_targets(ctx):
    out = []
    for t in ctx.store.list(scope=_TARGETS_SCOPE):
        v = t.get("value") or {}
        out.append({"host": v.get("host"), "port": v.get("port"), "ts": v.get("ts")})
    out.sort(key=lambda x: x.get("ts") or 0, reverse=True)
    return out


def _remember_target(ctx, host, port):
    """Mémorise (host, port) en tête, sans doublon, borné à _MAX_TARGETS."""
    existing = ctx.store.list(scope=_TARGETS_SCOPE)
    for t in existing:
        v = t.get("value") or {}
        if v.get("host") == host and int(v.get("port") or 0) == port:
            ctx.store.update(t["id"], value={"host": host, "port": port, "ts": int(time.time())})
            return
    ctx.store.create("%s:%d" % (host, port),
                     {"host": host, "port": port, "ts": int(time.time())},
                     scope=_TARGETS_SCOPE)
    # élagage : garder les _MAX_TARGETS plus récents
    items = ctx.store.list(scope=_TARGETS_SCOPE)
    if len(items) > _MAX_TARGETS:
        items.sort(key=lambda t: (t.get("value") or {}).get("ts") or 0)
        for t in items[:len(items) - _MAX_TARGETS]:
            ctx.store.delete(t["id"])
