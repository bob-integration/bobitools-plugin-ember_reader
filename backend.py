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
browse = _client.browse
set_value = _client.set_value

# Cibles récemment interrogées, persistées dans le store scellé à l'outil (confort UI).
_TARGETS_SCOPE = "target"
_MAX_TARGETS = 20


def api(path, method, payload, ctx):
    parts = [p for p in (path or "").split("/") if p]
    payload = payload or {}

    if parts == ["browse"] and method == "POST":
        return _browse(payload, ctx)
    if parts == ["crawl"] and method == "POST":
        return _crawl(payload, ctx)
    if parts == ["set"] and method == "POST":
        return _set(payload, ctx)
    if parts == ["targets"] and method == "GET":
        return {"targets": _list_targets(ctx)}
    if parts == ["targets"] and method == "DELETE":
        for t in ctx.store.list(scope=_TARGETS_SCOPE):
            ctx.store.delete(t["id"])
        return {"ok": True}
    return 404, {"error": "route inconnue"}


def _cible(payload):
    """(hôte, port, délai) validés, ou (None, message)."""
    host = (str(payload.get("host") or "")).strip()
    if not host:
        return None, "hôte requis"
    try:
        port = int(payload.get("port") or 9000)
    except (TypeError, ValueError):
        return None, "port invalide"
    if not (1 <= port <= 65535):
        return None, "port hors bornes"
    try:
        timeout = float(payload.get("timeout") or 8.0)
    except (TypeError, ValueError):
        timeout = 8.0
    return (host, port, max(1.0, min(120.0, timeout))), None


def _browse(payload, ctx):
    """Enfants directs d'UN nœud. Lecture par défaut de l'outil : on n'ouvre que ce qu'on
    regarde, au lieu de parcourir tout l'arbre pour afficher trois lignes."""
    cible, motif = _cible(payload)
    if motif:
        return 400, {"error": motif}
    host, port, timeout = cible
    chemin = payload.get("path") or []
    if not isinstance(chemin, list):
        return 400, {"error": "chemin invalide"}
    try:
        chemin = [int(x) for x in chemin]
    except (TypeError, ValueError):
        return 400, {"error": "chemin invalide"}

    t0 = time.time()
    enfants, err, tronque = browse(host, port, chemin, timeout=timeout)
    elapsed = round((time.time() - t0) * 1000)
    if err:
        return {"ok": False, "error": err, "host": host, "port": port, "elapsed_ms": elapsed}
    if not chemin:
        _remember_target(ctx, host, port)     # la racine seulement : pas une cible par dépliage
        ctx.audit("browse", "%s:%d" % (host, port))
    avert = []
    if tronque:
        avert.append("réponse INCOMPLÈTE : le nœud n'a pas fini de répondre en %g s. "
                     "Augmentez le délai." % timeout)
    return {"ok": True, "host": host, "port": port, "path": chemin, "tree": enfants,
            "count": len(enfants), "elapsed_ms": elapsed, "partial": bool(tronque),
            "warnings": avert}


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
    # Plafond porté de 20 à 120 s : un provider à grille pleine pousse plusieurs mégaoctets à
    # la connexion (2,6 Mo mesurés sur le nôtre, 14 s de relevé), et 20 s ne suffisaient plus.
    timeout = max(1.0, min(120.0, timeout))

    ctx.audit("crawl", "%s:%d" % (host, port))
    t0 = time.time()
    tree, err, limites = crawl(host, port, timeout=timeout)
    elapsed = round((time.time() - t0) * 1000)
    if err:
        return {"ok": False, "error": err, "host": host, "port": port, "elapsed_ms": elapsed}

    _remember_target(ctx, host, port)
    nodes = sum(1 for e in tree.values() if e["kind"] == "node")
    params = sum(1 for e in tree.values() if e["kind"] == "param")
    # Un relevé ÉCOURTÉ n'est pas un succès : le dire, et dire quoi faire. Sans ça, un arbre
    # tronqué par le délai est indiscernable d'un provider vide, et on cherche le défaut du
    # mauvais côté (une heure perdue le 2026-07-31 sur notre propre provider).
    avert = []
    if limites.get("timeout"):
        avert.append("relevé INCOMPLET : un nœud n'a pas répondu en %g s. Augmentez le délai — "
                     "un provider volumineux met plusieurs secondes à pousser son arbre."
                     % limites["timeout"])
    if limites.get("max_nodes"):
        avert.append("exploration arrêtée à %d nœuds (plafond du relevé)." % limites["max_nodes"])
    if limites.get("max_depth"):
        avert.append("des branches au-delà de %d niveaux n'ont pas été explorées."
                     % limites["max_depth"])
    if not tree:
        avert.append("aucun élément relevé : le provider a accepté la connexion mais rien "
                     "n'a pu être décodé.")
    return {"ok": True, "partial": bool(avert), "warnings": avert,
            "host": host, "port": port, "elapsed_ms": elapsed,
            "count": len(tree), "nodes": nodes, "params": params, "tree": tree}


def _set(payload, ctx):
    """Écrit une valeur sur un paramètre RW. `type` (string|int|bool|real) fixe le type BER —
    l'UI le déduit de la valeur relevée, pour ne pas écrire une chaîne à la place d'un entier."""
    host = (str(payload.get("host") or "")).strip()
    if not host:
        return 400, {"error": "hôte requis"}
    try:
        port = int(payload.get("port") or 9000)
    except (TypeError, ValueError):
        return 400, {"error": "port invalide"}
    raw_path = payload.get("path")
    try:
        path = [int(x) for x in str(raw_path).split(".") if x != ""]
    except (TypeError, ValueError):
        path = []
    if not path:
        return 400, {"error": "chemin invalide"}
    value = payload.get("value")
    vtype = (payload.get("type") or "string").lower()
    try:
        if vtype in ("int", "integer"):
            value = int(value)
        elif vtype == "bool":
            value = value in (True, "true", "1", 1)
        elif vtype == "real":
            value = float(value)
        else:
            value = "" if value is None else str(value)
    except (TypeError, ValueError):
        return 400, {"error": "valeur incompatible avec le type %s" % vtype}

    ctx.audit("set", "%s:%d %s = %r" % (host, port, ".".join(map(str, path)), value))
    ok, msg = set_value(host, port, path, value)
    return {"ok": ok, "message": msg, "path": ".".join(map(str, path)), "value": value}


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
