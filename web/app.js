/* cartograph — the view layer.
 *
 * One rule runs through all of it: a fact and a judgement never look the same.
 * AST-derived elements render solid and green; inferred groupings and names
 * render dashed and violet. Coverage and staleness are always on screen, because
 * a map that hides what it could not see reports a tidier system than the one
 * you have.
 */
const $ = (s) => document.querySelector(s);
const api = (p, o) => fetch(p, o).then(async (r) => {
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
});

let cy = null, LEVEL = "cluster", ROOT = "", WIRING = null;
let RES = 0.6, TESTS = false, PROJECT = null;

/* ---------- graph ---------- */

const STYLE = [
  { selector: "node", style: {
      "background-color": "#101a15", "border-width": 1.5,
      label: "data(label)", color: "#d6e4dc", "font-family": "monospace", "font-size": 11,
      "text-valign": "center", "text-wrap": "wrap", "text-max-width": 150,
      shape: "round-rectangle", width: "label", height: "label",
      padding: "11px", "border-color": "#4ade80",
  }},
  // Inferred nodes are dashed. You can see at a glance which boxes are a story.
  { selector: 'node[prov = "inferred"]', style: {
      "border-color": "#c084fc", "border-style": "dashed",
      "background-color": "#150f1c", color: "#e9d5ff",
  }},
  { selector: 'node[kind = "cluster"]', style: {
      "font-size": 12, "font-weight": "bold", padding: "20px",
      width: "mapData(size, 1, 60, 70, 210)", height: "mapData(size, 1, 60, 46, 120)",
      shape: "round-rectangle", "text-max-width": 190 }},
  { selector: "node:selected", style: { "border-width": 3, "border-color": "#5eead4" }},

  { selector: "edge", style: {
      width: "data(w)", "line-color": "#2f4a3c", "target-arrow-color": "#2f4a3c",
      "target-arrow-shape": "triangle", "curve-style": "bezier",
      "arrow-scale": 0.85, opacity: 0.8,
  }},
  { selector: 'edge[prov = "ast"]', style: { "line-color": "#3d7a5a", "target-arrow-color": "#3d7a5a" }},
  { selector: 'edge[prov = "inferred"]', style: {
      "line-style": "dashed", "line-color": "#7e57a8", "target-arrow-color": "#7e57a8" }},
  { selector: 'edge[kind = "imports"]', style: { opacity: 0.35 }},
  { selector: "edge:selected", style: { "line-color": "#5eead4", "target-arrow-color": "#5eead4", opacity: 1 }},
];

async function crawl(refresh) {
  ROOT = $("#root").value.trim();
  $("#cov").textContent = "crawling…";
  try {
    const g = await api(`/api/graph?root=${encodeURIComponent(ROOT)}&level=${LEVEL}`
      + `&refresh=${!!refresh}&resolution=${RES}&include_tests=${TESTS}&min_members=3`);
    render(g);
    loadWiring();
    loadProject();
  } catch (e) {
    $("#cov").textContent = "failed";
    $("#cov").className = "badge bad";
    $("#p-detail").innerHTML = `<div class="empty" style="color:#f87171">${esc(e.message)}</div>`;
  }
}

function render(g) {
  const elements = [
    ...g.nodes.map((n) => ({ data: {
      id: n.id, label: n.label, kind: n.kind,
      prov: n.provenance.kind, note: n.provenance.note || "",
      path: n.path || "", meta: n.meta || {},
      size: (n.meta || {}).member_count || 1,
    }})),
    ...g.edges.map((e) => ({ data: {
      id: e.id, source: e.source, target: e.target, kind: e.kind,
      prov: e.provenance.kind, w: Math.min(9, 1 + Math.log2(1 + (e.weight || 1))),
      weight: e.weight, ev: e.provenance.evidence || [], note: e.provenance.note || "",
    }})),
  ];

  if (cy) cy.destroy();
  cy = cytoscape({
    container: $("#cy"), elements, style: STYLE,
    layout: LEVEL === "cluster"
      ? { name: "concentric", animate: false, padding: 60, minNodeSpacing: 55,
          concentric: (n) => (n.data("meta") || {}).member_count || 1,
          levelWidth: () => 12 }
      : { name: "cose", animate: false, nodeRepulsion: 12000,
          idealEdgeLength: 150, padding: 45, nodeDimensionsIncludeLabels: true },
    wheelSensitivity: 0.22,
  });

  cy.on("tap", "node", (ev) => inspectNode(ev.target.id()));
  cy.on("tap", "edge", (ev) => inspectEdge(ev.target.data()));
  cy.on("dbltap", "node", (ev) => {
    // Drilling in is the point of the top level — double-click descends.
    if (ev.target.data("kind") === "cluster" && LEVEL === "cluster") setLevel("module");
  });

  const c = g.coverage;
  const pct = Math.round((c.call_resolution_rate || 0) * 100);
  const cov = $("#cov");
  cov.textContent = `${c.modules} modules · ${g.edges.length} edges · ${pct}% calls resolved`;
  cov.className = "badge " + (pct >= 70 ? "ok" : pct >= 40 ? "warn" : "bad");
  cov.title =
    `${c.call_sites} call sites total: ${c.builtin_calls} builtin, ` +
    `${c.local_method_calls} local/method, ${c.external_calls} third-party — ` +
    `none of which can be a cross-module edge.\n` +
    `Of the ${c.candidate_calls} that COULD be, ${c.resolved_calls} resolved and ` +
    `${c.unresolved_calls} did not.\n\n` +
    `An ABSENT edge means static analysis did not find it, NOT that the call ` +
    `does not happen.` +
    (c.files_failed ? `\n${c.files_failed} file(s) failed to parse.` : "");

  const lang = $("#lang");
  lang.textContent = `${g.language} ${Math.round((g.language_confidence || 0) * 100)}%`
    + (g.is_mixed ? ` · ${Math.round(g.unmapped_fraction * 100)}% unmapped` : "");
  lang.className = "badge " + (g.is_mixed ? "warn" : "ok");
  lang.title = g.is_mixed
    ? `${Math.round(g.unmapped_fraction * 100)}% of source files are not ${g.language}. `
      + `Those files are NOT on this map and nothing here describes them.`
    : `${g.language} covers ${Math.round((g.language_confidence || 0) * 100)}% of source files.`;

  const age = $("#age");
  age.textContent = g.stale ? `crawled ${Math.round(g.age_seconds)}s ago — stale` : "fresh";
  age.className = "badge " + (g.stale ? "warn" : "ok");
}

/* ---------- inspector ---------- */

async function inspectNode(id) {
  const d = await api(`/api/node/${encodeURIComponent(id)}?root=${encodeURIComponent(ROOT)}`
    + `&resolution=${RES}&include_tests=${TESTS}`);
  const n = d.node, p = n.provenance;
  const members = (n.meta && n.meta.members) || [];

  let h = `<h2>${esc(n.kind)}</h2><h3 style="margin:0 0 6px;font-size:15px">${esc(n.label)}</h3>
    <div class="tiny muted" style="word-break:break-all">${esc(n.id)}</div>
    <p><span class="prov ${p.kind}">${p.kind === "ast" ? "verified" : "inferred"}</span></p>`;

  if (p.note) h += `<p class="why">${esc(p.note)}</p>`;

  if (p.kind === "inferred") {
    h += `<p class="why" style="color:#c084fc">This block is a judgement about where a
      boundary lies. The arrows into and out of it are facts; which modules sit inside
      it is not.</p>`;
    if (n.meta && n.meta.mechanical_label)
      h += `<div class="row"><span class="muted">before naming</span>
            <span>${esc(n.meta.mechanical_label)}</span></div>`;
  }

  if (n.path) h += `<h2>Source</h2><a class="cite" onclick="showSource('${esc(n.path)}',${n.lineno || 1})">${esc(n.path)}:${n.lineno || 1}</a>`;

  if (members.length) {
    h += `<h2>Contains ${members.length}</h2>`;
    h += members.map((m) => `<div class="tiny">${esc(m)}</div>`).join("");
  }
  if (d.children.length && !members.length) {
    h += `<h2>Defines ${d.children.length}</h2>`;
    h += d.children.slice(0, 60).map((c) =>
      `<a class="cite" onclick="showSource('${esc(c.path)}',${c.lineno})">${esc(c.label)} — ${esc(c.path)}:${c.lineno}</a>`).join("");
  }

  h += edgeList("Calls out / imports", d.out_edges, "target");
  h += edgeList("Called by / imported by", d.in_edges, "source");
  $("#p-detail").innerHTML = h;
  tab("detail");
}

function edgeList(title, edges, side) {
  if (!edges.length) return `<h2>${title}</h2><div class="tiny muted">none found statically</div>`;
  let h = `<h2>${title} (${edges.length})</h2>`;
  for (const e of edges.slice(0, 40)) {
    h += `<div class="row"><span>${esc(e[side])}</span>
      <span class="tiny muted">${esc(e.kind)} ×${e.weight}</span></div>`;
    for (const ev of (e.provenance.evidence || []).slice(0, 3)) {
      const [f, l] = ev.split(":");
      h += `<a class="cite" onclick="showSource('${esc(f)}',${parseInt(l) || 1})">↳ ${esc(ev)}</a>`;
    }
  }
  return h;
}

function inspectEdge(e) {
  let h = `<h2>edge · ${esc(e.kind)}</h2>
    <h3 style="margin:0 0 6px;font-size:14px">${esc(e.source)} → ${esc(e.target)}</h3>
    <p><span class="prov ${e.prov}">${e.prov === "ast" ? "verified" : "inferred"}</span>
       <span class="muted tiny"> ${e.weight} site(s)</span></p>`;
  if (e.note) h += `<p class="why">${esc(e.note)}</p>`;
  h += `<h2>Every site behind this arrow</h2>`;
  h += (e.ev || []).slice(0, 120).map((ev) => {
    const [f, l] = ev.split(":");
    return `<a class="cite" onclick="showSource('${esc(f)}',${parseInt(l) || 1})">${esc(ev)}</a>`;
  }).join("") || `<div class="tiny muted">no evidence recorded</div>`;
  $("#p-detail").innerHTML = h;
  tab("detail");
}

async function showSource(path, line) {
  const start = Math.max(1, line - 8);
  const d = await api(`/api/source?root=${encodeURIComponent(ROOT)}&path=${encodeURIComponent(path)}&start=${start}&end=${line + 16}`);
  const body = d.lines.map((t, i) => {
    const ln = d.start + i;
    const hit = ln === line;
    return `<span class="ln">${String(ln).padStart(5)}</span>  ${
      hit ? `<span style="color:#4ade80">${esc(t)}</span>` : esc(t)}`;
  }).join("\n");
  const pre = `<h2>${esc(path)}:${line}</h2><pre>${body}</pre>`;
  $("#p-detail").insertAdjacentHTML("afterbegin", pre);
  $("#p-detail").scrollTop = 0;
}

/* ---------- wiring ---------- */

async function loadWiring() {
  try {
    WIRING = await api(`/api/wiring?root=${encodeURIComponent(ROOT)}`);
  } catch { return; }
  const c = WIRING.counts || {};
  const order = ["dead", "mutated_before_read", "write_only", "wired"];
  const titles = {
    dead: "Dead — defined, read nowhere",
    mutated_before_read: "Written and read — the stored value may not be what ran",
    write_only: "Written, never read",
    wired: "Wired",
  };

  let h = `<h2>Parameter wiring</h2>
    <p class="muted tiny">Which knobs are actually connected. Every verdict is a
    countable property of the source with file:line on both sides — not an opinion.</p>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin:8px 0 14px">` +
    order.map((v) => `<span class="badge ${v === "dead" ? "bad" : v === "wired" ? "ok" : "warn"}">${v}: ${c[v] || 0}</span>`).join("") +
    `</div>`;

  for (const v of ["dead", "mutated_before_read"]) {
    const ps = WIRING.parameters.filter((p) => p.verdict === v);
    if (!ps.length) continue;
    h += `<h2>${titles[v]} — ${ps.length}</h2>`;
    if (v === "mutated_before_read")
      h += `<p class="why">High counts here are expected: any string-keyed dict write
        matches. Treat it as a place to look, not a list of defects.</p>`;
    for (const p of ps.slice(0, 60)) {
      h += `<div class="card ${v === "dead" ? "dead" : "mutated"}">
        <h3>${esc(p.name)} <span class="tiny muted">${esc(p.kind)}</span></h3>`;
      if (p.literal) h += `<div class="tiny muted">= ${esc(p.literal)}</div>`;
      for (const s of (p.defined_at || []).slice(0, 2)) {
        const [f, l] = s.split(":");
        h += `<a class="cite" onclick="showSource('${esc(f)}',${parseInt(l) || 1})">defined ${esc(s)}</a>`;
      }
      for (const s of (p.written_at || []).slice(0, 3)) {
        const [f, l] = s.split(":");
        h += `<a class="cite" onclick="showSource('${esc(f)}',${parseInt(l) || 1})">written ${esc(s)}</a>`;
      }
      h += `<p class="why">${esc(p.why)}</p></div>`;
    }
  }
  $("#p-wiring").innerHTML = h;
}

/* ---------- roster ---------- */

async function loadProject() {
  try {
    PROJECT = await api(`/api/project?root=${encodeURIComponent(ROOT)}`);
  } catch { return; }
  const roles = PROJECT.roles.roles;

  // Seat picker. Default to the least-powerful seat that can investigate, so
  // the dangerous capabilities are never the path of least resistance.
  const sel = $("#role");
  sel.innerHTML = `<option value="">(no seat — plain question)</option>` +
    roles.map((r) => `<option value="${esc(r.name)}">${esc(r.name)}</option>`).join("");
  sel.onchange = () => {
    const r = roles.find((x) => x.name === sel.value);
    $("#role-note").innerHTML = r
      ? `${esc(r.purpose)}<br><span style="color:#f87171">must not: ${
          esc((r.must_not || []).join(", ")) || "—"
        }</span>`
      : "";
  };

  let h = `<h2>Roster</h2>
    <p class="muted tiny">${PROJECT.configured
      ? `From <code>cartograph.toml</code>.`
      : `Default roster — no <code>cartograph.toml</code> in this repo.`}
    A seat is refused a capability it does not hold, at the API, before the model sees the task.</p>`;

  for (const r of roles) {
    const may = Object.entries(r.capabilities).filter(([, v]) => v).map(([c]) => c);
    // WITHHELD is what the roster explicitly forbids. UNHELD is merely absent.
    // Rendering them the same was a bug: it showed the human operator as
    // "must not build", which is not what the roster says. A policy display
    // that turns silence into prohibition is making claims nobody wrote.
    const withheld = r.must_not || [];
    const unheld = Object.entries(r.capabilities)
      .filter(([c, v]) => !v && !withheld.includes(c)).map(([c]) => c);
    h += `<div class="card">
      <h3>${esc(r.name)} ${r.escalates_to
        ? `<span class="tiny muted">→ ${esc(r.escalates_to)}</span>`
        : `<span class="tiny muted">→ ends here</span>`}</h3>
      <p class="why" style="color:var(--ink)">${esc(r.purpose)}</p>
      <div class="row"><span class="tiny" style="color:#4ade80">may</span>
        <span class="tiny">${esc(may.join(", ")) || "—"}</span></div>
      <div class="row"><span class="tiny" style="color:#f87171">must not</span>
        <span class="tiny">${esc(withheld.join(", ")) || "—"}</span></div>
      ${unheld.length ? `<div class="row"><span class="tiny muted">not held</span>
        <span class="tiny muted">${esc(unheld.join(", "))}</span></div>` : ""}
      ${r.answers_with ? `<p class="why"><b>answers with</b> ${esc(r.answers_with)}</p>` : ""}
      ${r.notes ? `<p class="why">${esc(r.notes)}</p>` : ""}
    </div>`;
  }

  h += `<h2>Separation of duties</h2>
    <p class="why">The one invariant no single agent can check about itself:
    <b>the seat that raises a problem must not be the seat that resolves it.</b>
    If it is, that seat is the only one that can never be caught getting it wrong.</p>`;
  $("#p-roster").innerHTML = h;
}

/* ---------- ask ---------- */

$("#ask-go").onclick = async () => {
  const q = $("#q").value.trim();
  if (!q) return;
  const focus = cy && cy.$(":selected").length ? cy.$(":selected")[0].id() : null;
  $("#ans").innerHTML = `<p class="muted">asking claude in ${esc(ROOT)}…</p>`;
  try {
    const a = await api("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ root: ROOT, question: q, focus,
                             role: $("#role").value || null, capability: "investigate" }),
    });
    let h = `<div class="card">${a.role
      ? `<div class="tiny muted" style="margin-bottom:6px">seat: ${esc(a.role)}</div>` : ""
    }<p>${esc(a.text).replace(/\n/g, "<br>")}</p>`;
    if (!a.is_supported)
      h += `<p class="why" style="color:#fbbf24">UNSUPPORTED — no file:line returned.
            Treat this as a lead, not a finding.</p>`;
    if (a.error) h += `<p class="why" style="color:#fbbf24">${esc(a.error)}</p>`;
    for (const cIt of a.citations || []) {
      const [f, l] = cIt.split(":");
      h += `<a class="cite" onclick="showSource('${esc(f)}',${parseInt(l) || 1})">${esc(cIt)}</a>`;
    }
    h += `</div>`;
    $("#ans").innerHTML = h;
    if (cy && a.node_ids) {
      cy.$(":selected").unselect();
      a.node_ids.forEach((n) => cy.$id(n).select());
    }
  } catch (e) {
    $("#ans").innerHTML = `<p style="color:#f87171">${esc(e.message)}</p>`;
  }
};

/* ---------- propose ---------- */

$("#e-go").onclick = async () => {
  const body = {
    root: ROOT, kind: "add_module", name: $("#e-name").value.trim(),
    called_by: $("#e-by").value.split(",").map((s) => s.trim()).filter(Boolean),
    purpose: $("#e-purpose").value.trim(),
  };
  if (!body.name) return;
  const i = await api("/api/intent", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  $("#intent").innerHTML =
    `<h2>Intent — review before anyone writes code</h2><pre>${esc(i.prompt)}</pre>
     <button class="btn ghost" onclick="navigator.clipboard.writeText(${JSON.stringify(i.prompt)})">
     Copy for an agent</button>`;
};

/* ---------- chrome ---------- */

function tab(name) {
  document.querySelectorAll(".pane").forEach((p) => p.classList.remove("on"));
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.remove("on"));
  $(`#p-${name}`).classList.add("on");
  document.querySelector(`.tabs button[data-p="${name}"]`).classList.add("on");
}
document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => tab(b.dataset.p)));

function setLevel(l) {
  LEVEL = l;
  $("#lvl-cluster").classList.toggle("on", l === "cluster");
  $("#lvl-module").classList.toggle("on", l === "module");
  crawl(false);
}
$("#lvl-cluster").onclick = () => setLevel("cluster");
$("#res").oninput = (e) => { RES = parseFloat(e.target.value); $("#res-v").textContent = RES.toFixed(1); };
$("#res").onchange = () => crawl(false);
$("#tests").onchange = (e) => { TESTS = e.target.checked; crawl(false); };
$("#lvl-module").onclick = () => setLevel("module");
$("#crawl").onclick = () => crawl(true);

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
window.showSource = showSource;

crawl(false);
