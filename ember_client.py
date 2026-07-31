# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 BOBI SAS, France
# Auteur : Cyril Mazouer, pour le compte de BOBI SAS
# Distribué sous licence GNU GPL v3 (ou ultérieure) ; voir le fichier LICENSE.

"""Client Ember+ autonome (S101 + BER/Glow), pour LIRE l'arbre d'un provider tiers.

Volontairement AUTONOME : le plugin est distribuable en submodule sans dépendre du
service Ember+ de Bobi.Tools. Les primitives BER/S101 sont reprises verbatim de
`services/emberplus/emberplus_glow.py` (encodeur provider), élaguées de tout ce qui ne
sert pas à un consommateur : ici on n'ENCODE qu'un GetDirectory et on DÉCODE des
QualifiedNode/QualifiedParameter.

Un provider Ember+ expose un arbre plat de nœuds/paramètres à chemins RELATIVE-OID. Ce
client fait un GetDirectory racine, puis un GetDirectory ciblé sur chaque nœud rencontré
(l'arbre n'est pas renvoyé en entier d'un coup : chaque niveau se demande explicitement).
"""
import math
import socket
import struct
import time

# ─── Constantes S101 / Glow (cf. libs101 / libember) ───────────────────
BOF, EOF, CE, XOR = 0xFE, 0xFF, 0xFD, 0x20
S101_MSG_EMBER = 0x0E
S101_CMD_PAYLOAD = 0x00
S101_CMD_KEEPALIVE_REQ = 0x01
S101_CMD_KEEPALIVE_RESP = 0x02
S101_VERSION = 0x01
DTD_GLOW = 0x01
GLOW_APP_BYTES = bytes([0x1F, 0x02])
PKG_FIRST, PKG_LAST, PKG_EMPTY = 0x80, 0x40, 0x20
S101_MAX_PAYLOAD_PER_PACKET = 1024

G_COMMAND = 2
G_ELEMENT_COLLECTION = 4
G_QUAL_PARAMETER = 9
G_QUAL_NODE = 10
G_ROOT_ELEMENT_COLLECTION = 11
CMD_GET_DIRECTORY = 32
PC_VALUE = 2                    # ParameterContents : champ Value (pour un SetValue)

U_BOOL, U_INTEGER, U_UTF8, U_REAL = 1, 2, 12, 9

# ─── Encodeur BER (le strict nécessaire pour un GetDirectory) ──────────
def _ber_len(n):
    if n < 128:
        return bytes([n])
    out = bytearray()
    while n:
        out.insert(0, n & 0xFF)
        n >>= 8
    return bytes([0x80 | len(out)]) + bytes(out)

def _tlv(tag_byte, content):
    return bytes([tag_byte]) + _ber_len(len(content)) + content

def _app_constructed(tag_num, content):
    return _tlv(0x60 | tag_num, content)

def _ctx_constructed(tag_num, content):
    return _tlv(0xA0 | tag_num, content)

def _ctx_explicit(tag_num, universal_tlv):
    return _ctx_constructed(tag_num, universal_tlv)

def _int_bytes(value):
    value = int(value)
    if value == 0:
        return b"\x00"
    nbytes = 1
    while True:
        try:
            data = value.to_bytes(nbytes, "big", signed=True)
            break
        except OverflowError:
            nbytes += 1
    while len(data) > 1 and ((data[0] == 0x00 and not (data[1] & 0x80)) or
                             (data[0] == 0xFF and (data[1] & 0x80))):
        data = data[1:]
    return data

def _universal_primitive(tag_num, content):
    return _tlv(tag_num & 0x1F, content)

def ber_int(value):
    return _universal_primitive(U_INTEGER, _int_bytes(value))

def _relative_oid_bytes(path):
    out = bytearray()
    for n in path:
        n = int(n)
        if n == 0:
            out.append(0)
            continue
        chunk = []
        while n:
            chunk.insert(0, n & 0x7F)
            n >>= 7
        for i in range(len(chunk) - 1):
            chunk[i] |= 0x80
        out.extend(chunk)
    return bytes(out)

def ber_relative_oid(path):
    return _universal_primitive(13, _relative_oid_bytes(path))

def ber_utf8(s):
    return _universal_primitive(U_UTF8, (s or "").encode("utf-8"))

def ber_bool(v):
    return _universal_primitive(U_BOOL, b"\xff" if v else b"\x00")

def _real_bytes(value):
    if value == 0.0:
        return b""
    if math.isinf(value):
        return b"\x40" if value > 0 else b"\x41"
    if math.isnan(value):
        return b"\x42"
    bits = struct.unpack(">Q", struct.pack(">d", value))[0]
    sign = (bits >> 63) & 1
    raw_exp = (bits >> 52) & 0x7FF
    raw_mant = bits & ((1 << 52) - 1)
    if raw_exp == 0:
        exponent, mantissa = -1074, raw_mant
    else:
        exponent, mantissa = raw_exp - 1023 - 52, raw_mant | (1 << 52)
    while mantissa and (mantissa & 1) == 0:
        mantissa >>= 1
        exponent += 1
    if mantissa == 0:
        return b""
    mantissa_bytes = mantissa.to_bytes(max(1, (mantissa.bit_length() + 7) // 8), "big")
    if exponent >= 0:
        nb_exp = max(1, (exponent.bit_length() + 8) // 8)
    else:
        nb_exp = max(1, ((-exponent - 1).bit_length() + 8) // 8)
    exp_bytes = exponent.to_bytes(nb_exp, "big", signed=True)
    cb = 0x80 | (0x40 if sign else 0) | min(nb_exp - 1, 3)
    if nb_exp <= 3:
        return bytes([cb]) + exp_bytes + mantissa_bytes
    return bytes([cb, nb_exp]) + exp_bytes + mantissa_bytes

def ber_real(value):
    return _universal_primitive(U_REAL, _real_bytes(value))

def _encode_value(value):
    """Valeur Python → primitive BER universelle, selon son type. bool AVANT int (bool ⊂ int)."""
    if isinstance(value, bool):
        return ber_bool(value)
    if isinstance(value, int):
        return ber_int(value)
    if isinstance(value, float):
        return ber_real(value)
    return ber_utf8(str(value))

# ─── Décodeur BER ──────────────────────────────────────────────────────
def _decode_len(buf, i):
    b = buf[i]
    i += 1
    if b < 128:
        return b, i
    nb = b & 0x7F
    n = 0
    for _ in range(nb):
        n = (n << 8) | buf[i]
        i += 1
    return n, i

def _decode_tag(buf, i):
    b = buf[i]
    klass = (b >> 6) & 0x3
    constructed = bool(b & 0x20)
    num = b & 0x1F
    i += 1
    if num == 0x1F:  # tag long form
        num = 0
        while True:
            num = (num << 7) | (buf[i] & 0x7F)
            more = buf[i] & 0x80
            i += 1
            if not more:
                break
    return klass, constructed, num, i

def ber_iter(buf, start=0, end=None):
    if end is None:
        end = len(buf)
    i = start
    while i < end:
        klass, constructed, num, i = _decode_tag(buf, i)
        length, i = _decode_len(buf, i)
        yield klass, constructed, num, buf[i:i + length]
        i += length

def parse_int(content):
    return int.from_bytes(content, "big", signed=True) if content else 0

def parse_utf8(content):
    return content.decode("utf-8", "replace")

def parse_real(content):
    # IEEE 754 base-2 BER real (suffisant pour un afficheur ; 0 si vide)
    if not content:
        return 0.0
    first = content[0]
    if first & 0x80 == 0:
        return 0.0  # forme décimale/spéciale non gérée → 0
    sign = -1 if (first & 0x40) else 1
    exp_len = (first & 0x03) + 1
    exp = int.from_bytes(content[1:1 + exp_len], "big", signed=True)
    mant = int.from_bytes(content[1 + exp_len:], "big")
    return sign * mant * (2.0 ** exp)

def parse_relative_oid(content):
    out = []
    n = 0
    for b in content:
        n = (n << 7) | (b & 0x7F)
        if not (b & 0x80):
            out.append(n)
            n = 0
    return out

# ─── S101 (framing) ────────────────────────────────────────────────────
def _crc16_ccitt(data):
    """CRC-16/X-25 (poly réfléchi 0x8408, init 0xFFFF, reflected I/O, final XOR) — libs101."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if (crc & 1) else (crc >> 1)
    return (crc ^ 0xFFFF) & 0xFFFF

S101_INVALID_LO = 0xF8

def _escape(buf):
    """Un octet ≥ 0xF8 (dont BOF/EOF/CE) est échappé : CE puis l'octet XOR 0x20."""
    out = bytearray()
    for b in buf:
        if b >= S101_INVALID_LO:
            out.append(CE)
            out.append(b ^ XOR)
        else:
            out.append(b)
    return bytes(out)

def _encode_s101_packet(chunk, flags):
    body = bytes([0x00, S101_MSG_EMBER, S101_CMD_PAYLOAD, S101_VERSION,
                  flags, DTD_GLOW, len(GLOW_APP_BYTES)]) + GLOW_APP_BYTES + chunk
    crc = _crc16_ccitt(body)
    body_with_crc = body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])
    return bytes([BOF]) + _escape(body_with_crc) + bytes([EOF])

def s101_encode_ember(payload):
    out = bytearray()
    n = len(payload)
    if n == 0:
        return bytes(_encode_s101_packet(b"", PKG_FIRST | PKG_LAST | PKG_EMPTY))
    off = 0
    first = True
    while off < n:
        chunk = payload[off:off + S101_MAX_PAYLOAD_PER_PACKET]
        off += len(chunk)
        flags = 0
        if first:
            flags |= PKG_FIRST
        if off >= n:
            flags |= PKG_LAST
        out += _encode_s101_packet(chunk, flags)
        first = False
    return bytes(out)

class S101Reader:
    """Réassemble les paquets S101 → payloads Ember+ (générateur de ('payload', bytes))."""
    def __init__(self):
        self._buf = bytearray()
        self._in = False
        self._esc = False
        self._acc = bytearray()
        self._active = False

    def feed(self, data):
        for b in data:
            if not self._in:
                if b == BOF:
                    self._in = True
                    self._buf.clear()
                continue
            if b == BOF:
                self._buf.clear()
                self._esc = False
                continue
            if b == EOF:
                yield from self._frame()
                self._in = False
                self._buf.clear()
                self._esc = False
                continue
            if self._esc:
                self._buf.append(b ^ XOR)
                self._esc = False
            elif b == CE:
                self._esc = True
            else:
                self._buf.append(b)

    def _frame(self):
        if len(self._buf) < 4:
            return
        plen = len(self._buf) - 2
        body = bytes(self._buf[:plen])
        lo, hi = self._buf[plen], self._buf[plen + 1]
        if (lo | (hi << 8)) != _crc16_ccitt(body):
            return
        if body[1] != S101_MSG_EMBER:
            return
        cmd = body[2]
        if cmd in (S101_CMD_KEEPALIVE_REQ, S101_CMD_KEEPALIVE_RESP):
            return
        if cmd != S101_CMD_PAYLOAD or len(body) < 7:
            return
        flags = body[4]
        app_count = body[6]
        payload = body[7 + app_count:]
        if flags & PKG_FIRST:
            self._acc = bytearray(payload)
            self._active = True
        elif self._active:
            self._acc.extend(payload)
        else:
            return
        if (flags & PKG_LAST) and self._active:
            yield ("payload", bytes(self._acc))
            self._acc = bytearray()
            self._active = False

# ─── Crawler d'arbre ───────────────────────────────────────────────────
def _getdir_frame(path):
    """GetDirectory racine (path vide) ou ciblé sur un nœud (QualifiedNode[path])."""
    cmd = _app_constructed(G_COMMAND, _ctx_explicit(0, ber_int(CMD_GET_DIRECTORY)))
    if not path:
        inner = _ctx_explicit(0, cmd)
    else:
        children = _app_constructed(G_ELEMENT_COLLECTION, _ctx_explicit(0, cmd))
        qn = _app_constructed(G_QUAL_NODE,
                              _ctx_explicit(0, ber_relative_oid(path)) +
                              _ctx_explicit(2, children))
        inner = _ctx_explicit(0, qn)
    coll = _app_constructed(G_ROOT_ELEMENT_COLLECTION, inner)
    return s101_encode_ember(_app_constructed(0, coll))

def _first(content):
    for _k, _c, _n, c in ber_iter(content):
        return c
    return None

def _decode_value(tlv):
    for _k, _c, num, content in ber_iter(tlv):
        if num == U_INTEGER:
            return parse_int(content)
        if num == U_UTF8:
            return parse_utf8(content)
        if num == U_BOOL:
            return content != b"\x00"
        if num == U_REAL:
            try:
                return parse_real(content)
            except Exception:
                return None
    return None

def _decode(payload, out):
    """Remplit out[tuple(path)] = {kind, id, value, enum, access}."""
    def element(content, is_param):
        path = ident = val = enum = None
        access = None
        children = None
        for k2, _c, n2, cc in ber_iter(content):
            if k2 == 2 and n2 == 0:               # path
                try:
                    path = parse_relative_oid(_first(cc))
                except Exception:
                    path = None
            elif k2 == 2 and n2 == 1:             # contents SET
                setu = _first(cc)
                if setu is not None:
                    for _kk, _cc, nn, ccc in ber_iter(setu):
                        if nn == 0:
                            try:
                                ident = parse_utf8(_first(ccc))
                            except Exception:
                                pass
                        elif nn == 2 and is_param:      # PC_VALUE
                            val = _decode_value(ccc)
                        elif nn == 5 and is_param:      # PC_ACCESS
                            access = _decode_value(ccc)
                        elif nn == 7:                   # PC_ENUMERATION
                            try:
                                enum = parse_utf8(_first(ccc))
                            except Exception:
                                pass
            elif k2 == 2 and n2 == 2:             # children
                children = cc
        return path, ident, val, enum, access, children

    def walk(buf):
        for klass, cons, num, content in ber_iter(buf):
            if klass == 1 and num in (G_QUAL_NODE, G_QUAL_PARAMETER):
                is_param = num == G_QUAL_PARAMETER
                path, ident, val, enum, access, children = element(content, is_param)
                if path is not None:
                    key = tuple(path)
                    prev = out.get(key, {})
                    out[key] = {
                        "kind": "param" if is_param else "node",
                        "id": ident or prev.get("id"),
                        "value": val if val is not None else prev.get("value"),
                        "enum": (enum or prev.get("enum") or "").split("\n") if (enum or prev.get("enum")) else None,
                        "access": access if access is not None else prev.get("access"),
                    }
                if children is not None:
                    walk(children)
            elif cons:                            # wrapper [ctx 0] / collection [app 4|11]
                walk(content)
    walk(payload)

def _setvalue_frame(path, value):
    """Trame SetValue consumer→provider : QualifiedParameter[path] dont les contents ne
    portent que la nouvelle Value ([Ctx PC_VALUE]). Le provider retrouve le paramètre par
    son chemin absolu et applique la valeur."""
    contents = _universal_constructed(17, _ctx_explicit(PC_VALUE, _encode_value(value)))
    qp = _app_constructed(G_QUAL_PARAMETER,
                          _ctx_explicit(0, ber_relative_oid(path)) +
                          _ctx_explicit(1, contents))
    coll = _app_constructed(G_ROOT_ELEMENT_COLLECTION, _ctx_explicit(0, qp))
    return s101_encode_ember(_app_constructed(0, coll))

def _universal_constructed(tag_num, content):
    return _tlv(0x20 | (tag_num & 0x1F), content)

def set_value(host, port, path, value, timeout=4.0, verify=True):
    """Écrit `value` sur le paramètre `path` (liste d'entiers) d'un provider Ember+.

    Renvoie (ok, message). Si `verify`, relit le paramètre après écriture et compare — un
    provider Ember+ n'ACQUITTE pas un SetValue, la seule confirmation fiable est la relecture.
    ATTENTION : écrit réellement sur l'équipement (ex. router un flux sur un Ross Newt)."""
    if not path:
        return False, "chemin vide"
    try:
        sock = socket.create_connection((host, int(port)), timeout=timeout)
    except Exception as e:
        return False, "connexion impossible : %s" % e
    sock.settimeout(timeout)
    try:
        sock.sendall(_setvalue_frame(list(path), value))
        # Laisser le provider appliquer + éventuellement pousser la nouvelle valeur.
        time.sleep(0.4)
        try:
            while True:
                if not sock.recv(65536):
                    break
        except socket.timeout:
            pass
        if not verify:
            return True, "écrit (non vérifié)"
        # Relecture ciblée du paramètre : GetDirectory sur son parent, on cherche `path`.
        parent = list(path[:-1])
        sock.sendall(_getdir_frame(parent))
        buf = b""
        got = False
        sock.settimeout(0.3)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                d = sock.recv(65536)
                if not d:
                    break
                buf += d
                got = True
            except socket.timeout:
                if got:
                    break
        out = {}
        for k, p in S101Reader().feed(buf):
            if k == "payload":
                _decode(p, out)
        cur = out.get(tuple(path), {}).get("value")
        if cur == value:
            return True, "écrit et vérifié"
        return True, "écrit, mais relecture = %r (attendu %r)" % (cur, value)
    finally:
        sock.close()

def browse(host, port, path=(), timeout=8.0):
    """Relève les enfants DIRECTS d'un seul nœud. Renvoie (enfants, error, tronque).

    C'est le mode de lecture par DÉFAUT de l'outil, et `crawl` devient l'exception. La raison
    est mesurée : un provider à grille pleine pousse plusieurs mégaoctets et met une dizaine de
    secondes à se laisser parcourir en entier, pour un écran où l'on ne regarde jamais qu'une
    branche. Lire à la demande rend la première image immédiate, et ne coûte que ce qu'on ouvre.

    `tronque` dit que la réponse de ce nœud n'était pas terminée quand le délai a expiré — même
    règle que `crawl` : un relevé écourté ne doit jamais passer pour un nœud vide."""
    try:
        sock = socket.create_connection((host, int(port)), timeout=timeout)
    except Exception as e:
        return {}, "connexion impossible : %s" % e, False
    sock.settimeout(timeout)
    tronque = False
    try:
        path = [int(x) for x in (path or [])]
        sock.sendall(_getdir_frame(path))
        # Même lecture par détection de SILENCE que `crawl` : on accumule tant que ça arrive,
        # et on sort au premier blanc APRÈS avoir reçu quelque chose.
        buf = b""
        got = False
        sock.settimeout(0.3)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                d = sock.recv(65536)
                if not d:
                    break
                buf += d
                got = True
            except socket.timeout:
                if got:
                    break
        else:
            tronque = True
        vus = {}
        for k, pl in S101Reader().feed(buf):
            if k == "payload":
                _decode(pl, vus)
    finally:
        sock.close()
    # Enfants DIRECTS seulement. Un provider répond souvent plus large que demandé (il pousse
    # au passage des branches voisines) ; tout garder ferait un arbre incohérent avec ce que
    # l'utilisateur a ouvert.
    n = len(path)
    enfants = {".".join(map(str, k)): v for k, v in vus.items()
               if len(k) == n + 1 and list(k[:n]) == path}
    return enfants, None, tronque


def crawl(host, port, timeout=4.0, max_depth=6, max_nodes=2000):
    """Relève l'arbre d'un provider Ember+. Renvoie (tree, error, limites).

    `limites` dit ce qui a été ÉCOURTÉ : `{"max_nodes": …}` si le plafond de nœuds a stoppé
    l'exploration, `{"timeout": …}` si un nœud n'a pas répondu dans le délai, `{"max_depth": …}`
    si des branches ont été laissées de côté.

    Pourquoi ce troisième retour. Un arbre écourté ressemblait exactement à un arbre vide :
    la fonction rendait `(tree, None)` et l'appelant annonçait un succès. Constaté le
    2026-07-31 sur notre propre provider — 2,6 Mo à pousser, le relevé abandonnait pendant le
    transfert et rendait « ok, 0 élément », ce qui envoie chercher le défaut chez le provider
    alors qu'il est chez le client. Un abandon doit se dire."""
    tree = {}
    limites = {}
    try:
        sock = socket.create_connection((host, int(port)), timeout=timeout)
    except Exception as e:
        return {}, "connexion impossible : %s" % e, {}
    sock.settimeout(timeout)
    try:
        seen = set()
        queue = [[]]
        while queue:
            if len(tree) >= max_nodes:
                limites["max_nodes"] = max_nodes
                break
            path = queue.pop(0)
            key = tuple(path)
            if len(path) > max_depth:
                limites["max_depth"] = max_depth
                continue
            if key in seen:
                continue
            seen.add(key)
            sock.sendall(_getdir_frame(path))
            # Lecture par détection de SILENCE : on accumule tant que des données arrivent,
            # et on sort dès un court blanc APRÈS avoir reçu quelque chose (réponse complète).
            # `timeout` reste le plafond si le provider ne répond pas du tout à ce nœud.
            buf = b""
            got = False
            sock.settimeout(0.3)
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    d = sock.recv(65536)
                    if not d:
                        break
                    buf += d
                    got = True
                except socket.timeout:
                    if got:
                        break        # données reçues puis silence → réponse du nœud terminée
            else:
                # Sorti par la DEADLINE, pas par le silence : la réponse de ce nœud est
                # incomplète, donc l'arbre l'est aussi. C'est le cas d'un provider volumineux
                # sur un délai trop court, et il ne doit surtout pas passer pour un succès.
                limites["timeout"] = timeout
            got = {}
            for k, p in S101Reader().feed(buf):
                if k == "payload":
                    _decode(p, got)
            for p, info in got.items():
                if p not in tree or tree[p].get("id") is None:
                    tree[p] = info
                if info["kind"] == "node" and p not in seen:
                    queue.append(list(p))
    finally:
        sock.close()
    return {".".join(map(str, k)): v for k, v in tree.items()}, None, limites
