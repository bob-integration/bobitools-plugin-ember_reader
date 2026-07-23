// SPDX-License-Identifier: GPL-3.0-or-later
// Ember+ Reader — front. Interroge le backend (/api/tools/ember_reader/crawl), qui parle
// Ember+ au provider cible. Affiche l'arbre relevé, repliable, avec filtre plein-texte.
window.BTTools = window.BTTools || {};
window.BTTools.ember_reader = (function () {
    let EL = null, CTX = null, TREE = null, collapsed = new Set();

    const esc = (s) => String(s == null ? "" : s)
        .replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const $ = (sel) => EL.querySelector(sel);

    function mount(el, ctx) {
        EL = el; CTX = ctx;
        $("#er-go").onclick = doCrawl;
        $("#er-host").addEventListener("keydown", (e) => { if (e.key === "Enter") doCrawl(); });
        $("#er-port").addEventListener("keydown", (e) => { if (e.key === "Enter") doCrawl(); });
        $("#er-filter").addEventListener("input", render);
        loadRecent();
    }
    function unmount() { EL = CTX = TREE = null; collapsed = new Set(); }

    async function loadRecent() {
        try {
            const d = await CTX.api("targets");
            const box = $("#er-recent");
            if (!d.targets || !d.targets.length) { box.innerHTML = ""; return; }
            box.innerHTML = '<span class="er-recent-lbl">Récents :</span>' + d.targets.map((t) =>
                `<button type="button" class="er-chip" data-h="${esc(t.host)}" data-p="${t.port}">${esc(t.host)}:${t.port}</button>`).join("");
            box.querySelectorAll(".er-chip").forEach((b) => b.onclick = () => {
                $("#er-host").value = b.dataset.h; $("#er-port").value = b.dataset.p; doCrawl();
            });
        } catch (e) { /* pas bloquant */ }
    }

    async function doCrawl() {
        const host = $("#er-host").value.trim();
        const port = parseInt($("#er-port").value) || 9000;
        if (!host) { CTX.toast("Hôte requis", "error"); return; }
        $("#er-body").innerHTML = '<div class="er-empty">Lecture de ' + esc(host) + ':' + port + '…</div>';
        $("#er-meta").textContent = "";
        try {
            const d = await CTX.api("crawl", { method: "POST", body: { host, port } });
            if (!d.ok) {
                $("#er-body").innerHTML = '<div class="er-err">' + esc(d.error || "échec") + '</div>';
                return;
            }
            TREE = d.tree; collapsed = new Set();
            $("#er-meta").textContent = d.nodes + " nœuds · " + d.params + " params · " + d.elapsed_ms + " ms";
            $("#er-filter").hidden = false;
            render();
            loadRecent();
        } catch (e) {
            $("#er-body").innerHTML = '<div class="er-err">' + esc(e.message || e) + '</div>';
        }
    }

    // Chemins triés numériquement ("1.10" après "1.2"), en arbre indenté.
    const cmpPath = (a, b) => {
        const pa = a.split(".").map(Number), pb = b.split(".").map(Number);
        for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
            if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) - (pb[i] || 0);
        }
        return 0;
    };

    function render() {
        if (!TREE) return;
        const q = ($("#er-filter").value || "").trim().toLowerCase();
        let paths = Object.keys(TREE).sort(cmpPath);

        // Filtre : garde les éléments qui matchent (id/valeur), + leurs ancêtres pour le contexte.
        if (q) {
            const keep = new Set();
            for (const p of paths) {
                const e = TREE[p];
                const hay = ((e.id || "") + " " + (e.value == null ? "" : e.value)).toLowerCase();
                if (hay.includes(q)) {
                    const parts = p.split(".");
                    for (let i = 1; i <= parts.length; i++) keep.add(parts.slice(0, i).join("."));
                }
            }
            paths = paths.filter((p) => keep.has(p));
        }

        // Masque les descendants d'un nœud replié.
        const isHidden = (p) => {
            for (const c of collapsed) if (p !== c && p.startsWith(c + ".")) return true;
            return false;
        };

        const rows = [];
        for (const p of paths) {
            if (isHidden(p)) continue;
            const e = TREE[p];
            const depth = p.split(".").length - 1;
            const hasKids = e.kind === "node" && paths.some((x) => x.startsWith(p + ".") && x.split(".").length === depth + 2);
            const caret = hasKids ? (collapsed.has(p) ? "▸" : "▾") : "·";
            const rw = e.kind === "param" && e.access === 3;   // 3 = read-write
            const badge = e.kind === "param"
                ? (rw ? ' <span class="er-rw" data-edit="' + esc(p) + '" title="Modifier">RW ✎</span>'
                      : (e.access === 1 ? ' <span class="er-ro">RO</span>' : ""))
                : "";
            let val = "";
            if (e.kind === "param") {
                let v = e.value;
                if (e.enum && Number.isInteger(v) && e.enum[v] != null) v = e.enum[v] + " (" + v + ")";
                val = '<span class="er-val">' + esc(String(v).slice(0, 300)) + '</span>' + badge;
            }
            rows.push(
                '<div class="er-row er-' + e.kind + '" data-p="' + esc(p) + '" style="padding-left:' + (depth * 16 + 4) + 'px">' +
                '<span class="er-caret" data-toggle="' + esc(p) + '">' + caret + '</span>' +
                '<span class="er-path">' + esc(p) + '</span>' +
                '<span class="er-id">' + esc(e.id || "?") + '</span>' + val + '</div>');
        }
        $("#er-body").innerHTML = rows.join("") || '<div class="er-empty">Aucun résultat.</div>';
        $("#er-body").querySelectorAll(".er-caret[data-toggle]").forEach((c) => c.onclick = () => {
            const p = c.dataset.toggle;
            if (collapsed.has(p)) collapsed.delete(p); else collapsed.add(p);
            render();
        });
        $("#er-body").querySelectorAll(".er-rw[data-edit]").forEach((b) => b.onclick = () => editParam(b.dataset.edit));
    }

    // Type BER déduit de la valeur relevée : un provider attend le bon type sur un SetValue.
    function typeOf(v) {
        if (typeof v === "boolean") return "bool";
        if (typeof v === "number") return Number.isInteger(v) ? "int" : "real";
        return "string";
    }

    async function editParam(path) {
        const e = TREE[path];
        if (!e) return;
        const t = typeOf(e.value);
        const cur = e.value == null ? "" : String(e.value);
        const input = window.prompt("Écrire " + (e.id || path) + " (" + path + ") — type " + t + " :", cur);
        if (input === null) return;   // annulé
        if (!window.confirm("Écrire réellement sur l'équipement ?\n" + path + " = " + input)) return;
        const host = $("#er-host").value.trim();
        const port = parseInt($("#er-port").value) || 9000;
        try {
            const d = await CTX.api("set", { method: "POST", body: { host, port, path, value: input, type: t } });
            if (d.ok) {
                CTX.toast(d.message || "écrit");
                if (TREE[path]) TREE[path].value = (t === "int") ? parseInt(input) : (t === "real") ? parseFloat(input) : (t === "bool") ? (input === "true" || input === "1") : input;
                render();
            } else {
                CTX.toast(d.message || d.error || "échec", "error");
            }
        } catch (err) { CTX.toast(err.message || err, "error"); }
    }

    return { mount, unmount };
})();
