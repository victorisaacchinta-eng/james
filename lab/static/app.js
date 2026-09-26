// JAMES Workflow Lab frontend. No build step: plain ES module, talks to the FastAPI backend.
// Every rule is enforced by the backend; this UI only shows what the backend decides.

const S = {
  view: "workspace", role: "technician", meta: null, scenarios: [], mode: "fixture",
  scenario: "abnormal", asset: "DEMO-PUMP-03", question: "",
  jobId: null, job: null, evidence: null, guidance: null, steps: null, selStep: null,
  jobs: [], ledgerSel: null, ledgerEvents: [], reviewSel: null, dev: null, calls: [], lastError: null,
  citeCheck: null, busy: false,
  form: { summary: "", rnote: "", result: "done", reason: "", note: "", outcome: "REPORTED_RESOLVED" },
};
const EXAMPLES = [
  ["DEMO-PUMP-03", "Why is DEMO-PUMP-03 vibrating more than usual, and what should I check next?"],
  ["DEMO-PUMP-03", "Pump 3 bearing feels hot and noisy. What should I check?"],
  ["DEMO-PUMP-03", "How do I update the flow meter firmware?"],
  ["DEMO-FAN-01", "Why is the extract fan vibrating?"],
];
const LABEL = { vibration_mm_s: "Vibration", bearing_temp_c: "Bearing temperature", motor_current_a: "Motor current", flow_l_min: "Flow" };
const FACT = { isolation_confirmed: "Isolation (lockout/tagout) applied by an authorised person",
               ppe_confirmed: "Gloves and eye protection on" };

// ---------- helpers ----------
const $ = (s, r = document) => r.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pill = (state, text) => `<span class="pill s-${esc(state)}">${esc(text ?? String(state).replace(/_/g, " "))}</span>`;
const fmt = (n, d = 2) => {
  if (n === null || n === undefined) return "n/a";
  const s = Number(n).toFixed(d);
  return s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s;   // 50 stays 50; 2.50 becomes 2.5
};
const key = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random()));
const hhmm = (ts) => (ts ? ts.slice(11, 16) : "");

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toast.h); toast.h = setTimeout(() => (t.className = "toast"), err ? 6500 : 2600);
}

async function api(method, path, body, opts = {}) {
  const headers = { "X-Demo-Role": opts.role || S.role };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.idem) headers["Idempotency-Key"] = opts.idem;
  const t0 = performance.now();
  const r = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const ms = Math.round(performance.now() - t0);
  const text = await r.text();
  let data; try { data = JSON.parse(text); } catch { data = text; }
  S.calls.unshift({ at: new Date().toLocaleTimeString(), method, path, status: r.status, ms, request: body, response: data });
  S.calls.length = Math.min(S.calls.length, 40);
  if (!r.ok) {
    const e = new Error(typeof data === "object" ? data.error : text);
    e.status = r.status; e.data = data; S.lastError = { path, status: r.status, data };
    throw e;
  }
  return data;
}

async function run(fn) {
  if (S.busy) return;
  S.busy = true; render();
  try { await fn(); } catch (e) { toast(e.message, true); console.warn(e); }
  finally { S.busy = false; render(); }
}

// ---------- data flows ----------
async function loadJob(jobId) {
  S.jobId = jobId;
  S.job = await api("GET", `/api/jobs/${jobId}`);
  S.evidence = null; S.steps = null; S.guidance = null; S.selStep = null;
  try { S.evidence = await api("GET", `/api/jobs/${jobId}/evidence`); } catch (e) { S.evidence = { error: e.data || { error: e.message } }; }
  try { S.steps = await api("GET", `/api/jobs/${jobId}/steps`); } catch { S.steps = null; }
  const props = (await api("GET", `/api/jobs/${jobId}/export?format=json`)).proposals || [];
  S.guidance = props.length ? props[props.length - 1] : null;
}

async function startJob() {
  const q = S.question.trim();
  if (!q) return toast("Type a maintenance question first", true);
  const j = await api("POST", "/api/jobs", { scenario: S.scenario, asset_id: S.asset, question: q });
  S.jobId = j.job.job_id; S.job = j; S.guidance = null; S.steps = null; S.selStep = null; S.citeCheck = null;
  try {
    S.evidence = await api("GET", `/api/jobs/${S.jobId}/evidence`);
  } catch (e) {
    S.evidence = { error: e.data || { error: e.message } };
    S.job = await api("GET", `/api/jobs/${S.jobId}`);
    S.view = "evidence"; return;
  }
  await askGuidance(S.mode);
  S.view = "evidence";
}

async function askGuidance(mode) {
  try {
    const g = await api("POST", `/api/jobs/${S.jobId}/guidance`, { mode });
    S.guidance = g.proposal; S.steps = g; S.selStep = g.current;
    S.guidanceMs = g.timing_ms;
    checkCitations();
  } catch (e) {
    if (e.status === 503) {
      S.aiFailure = e.data;   // shown on screen with an explicit fixture option; never switched silently
    } else throw e;
  }
  S.job = await api("GET", `/api/jobs/${S.jobId}`);
}

async function checkCitations() {
  const refs = [...new Set((S.steps?.steps || []).map((s) => s.citation))];
  let ok = 0;
  for (const r of refs) { try { await api("GET", `/api/sources?ref=${encodeURIComponent(r)}`); ok++; } catch {} }
  S.citeCheck = { ok, total: refs.length }; render();
}

async function refreshSteps() {
  S.steps = await api("GET", `/api/jobs/${S.jobId}/steps`);
  S.job = await api("GET", `/api/jobs/${S.jobId}`);
}

// ---------- rendering ----------
function render() {
  document.querySelectorAll(".nav [data-view]").forEach((b) => b.classList.toggle("on", b.dataset.view === S.view));
  document.querySelectorAll(".roles .seg").forEach((b) => b.classList.toggle("on", b.dataset.role === S.role));
  const m = S.meta;
  const modeBadge = S.guidance?.mode === "local_ai" ? `<span class="badge">${esc(S.guidance.mode_label)}</span>`
    : `<span class="badge warn">${esc(m?.fixture_label || "Fixture mode")}</span>`;
  $("#badges").innerHTML = m ? `<span class="badge warn">Synthetic data</span>${modeBadge}
    ${Object.values(m.packs).map((p) => `<span class="badge">Pack ${esc(p)}</span>`).join("")}
    ${S.evidence?.sensor ? `<span class="badge">Scenario clock ${esc(S.evidence.sensor.scenario_clock.replace("T", " ").replace("Z", " UTC"))}</span>` : ""}` : "";
  const j = S.job?.job;
  $("#jobchip").innerHTML = j ? `Active job<br><b>${esc(j.job_id)}</b><br>${esc(j.asset_id)}<br>${pill(j.state)}` : "No active job";
  const views = { workspace, evidence, work, ledger, review, dev };
  $("#main").innerHTML = `<div class="fadein">${views[S.view]()}</div>`;
  bind();
}

function workspace() {
  const sc = S.scenarios.map((s) => `<option value="${esc(s.id)}" ${s.id === S.scenario ? "selected" : ""}>${esc(s.label)}</option>`).join("");
  const as = Object.entries(S.meta?.assets || {}).map(([id, a]) =>
    `<option value="${esc(id)}" ${id === S.asset ? "selected" : ""}>${esc(a.label)}${a.pack ? "" : " (no pack)"}</option>`).join("");
  const j = S.job;
  return `
  <h1>Ask about a demo machine</h1>
  <p class="lede">Pick a scenario and an asset, then ask a maintenance question. JAMES retrieves the sensor log, finds the
  procedure, and proposes steps it can source. Everything here is synthetic.</p>
  <div class="split">
    <div class="card">
      <label class="f" for="sc">Scenario</label><select id="sc">${sc}</select>
      <div style="height:12px"></div>
      <label class="f" for="as">Asset</label><select id="as">${as}</select>
      <div style="height:12px"></div>
      <label class="f">Guidance mode</label>
      <div class="row small">
        <label><input type="radio" name="mode" value="fixture" ${S.mode === "fixture" ? "checked" : ""}> Fixture (scripted, no AI)</label>
        <label><input type="radio" name="mode" value="local_ai" ${S.mode === "local_ai" ? "checked" : ""}> Local AI (Ollama, optional)</label>
      </div>
    </div>
    <div class="card">
      <label class="f" for="q">Maintenance question</label>
      <textarea id="q" maxlength="500" placeholder="Why is DEMO-PUMP-03 vibrating more than usual?">${esc(S.question)}</textarea>
      <div class="chips">${EXAMPLES.map(([a, q], i) => `<button class="chip" data-ex="${i}">${esc(q)}</button>`).join("")}</div>
      <div class="row" style="margin-top:14px">
        <button class="btn" id="start" ${S.busy ? "disabled" : ""}>${S.busy ? "Working..." : "Start job"}</button>
        <span class="muted small">Creates a job, retrieves evidence, then asks for guidance.</span>
      </div>
    </div>
  </div>
  ${j ? `<div class="card"><h2>Active job</h2>${jobSummary(j)}</div>` : ""}`;
}

function jobSummary(v) {
  const j = v.job;
  return `<div class="meta"><span>Job <b class="mono">${esc(j.job_id)}</b></span><span>Asset <b>${esc(j.asset_id)}</b></span>
    <span>Scenario <b>${esc(j.scenario || "seeded")}</b></span><span>State ${pill(j.state)}</span>
    <span>Outcome ${j.outcome ? pill(j.outcome) : "<b>open</b>"}</span><span>Review ${pill(j.review_status)}</span>
    <span>Record <b>${esc(v.completeness.score)}</b></span></div>
    <p class="small" style="margin:8px 0 0">"${esc(j.question)}"</p>`;
}

function chart(ch, series, sensor) {
  const vals = series.channels[ch.channel] || [];
  const pts = vals.map((v, i) => [i, v]).filter((p) => p[1] !== null && p[1] !== undefined);
  if (!pts.length) return `<div class="empty small">No readings to plot</div>`;
  const W = 560, H = 150, pad = 20;
  const ys = pts.map((p) => p[1]);
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (hi - lo < 1e-6) { lo -= 1; hi += 1; }
  const x = (i) => pad + (i / Math.max(1, vals.length - 1)) * (W - pad * 2);
  const y = (v) => H - pad - ((v - lo) / (hi - lo)) * (H - pad * 2);
  const t = series.t;
  const bi = t.findIndex((s) => s >= sensor.baseline_end); const ci = t.findIndex((s) => s >= sensor.current_start);
  const d = pts.map((p, k) => `${k ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
  const by = ch.baseline_mean != null ? y(ch.baseline_mean) : null;
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(LABEL[ch.channel])} over the window">
    ${bi > 0 ? `<rect class="band" x="${x(0)}" y="${pad / 2}" width="${x(bi) - x(0)}" height="${H - pad * 1.5}"/>` : ""}
    ${ci >= 0 ? `<rect class="cur" x="${x(ci)}" y="${pad / 2}" width="${x(vals.length - 1) - x(ci)}" height="${H - pad * 1.5}"/>` : ""}
    ${by != null ? `<line class="base" x1="${pad}" x2="${W - pad}" y1="${by}" y2="${by}"/>` : ""}
    <path class="line" d="${d}"/>
    <text x="${pad}" y="${H - 3}">${esc(hhmm(t[0]))}</text><text x="${W - pad}" y="${H - 3}" text-anchor="end">${esc(hhmm(t[t.length - 1]))} UTC</text>
    <text x="${pad}" y="10">${fmt(hi, 1)}</text><text x="${pad}" y="${H - pad + 2}">${fmt(lo, 1)}</text>
  </svg>`;
}

function evidence() {
  if (!S.jobId) return `<h1>Evidence</h1><div class="empty">Start a job in the Workspace first.</div>`;
  const e = S.evidence;
  if (!e) return `<h1>Evidence</h1><div class="empty">Loading...</div>`;
  if (e.error) {
    const probs = e.error.problems || [];
    return `<h1>Evidence</h1><div class="callout red"><p><b>Dataset rejected. Nothing was stored, and no guidance can be given
      from it.</b></p>${probs.map((p) => `<p class="mono small">${esc(p)}</p>`).join("")}</div>`;
  }
  const s = e.sensor;
  const cards = s.channels.map((c) => `
    <div class="card">
      <div class="row spread"><h3>${esc(LABEL[c.channel])}</h3>${pill(c.flag)}</div>
      ${c.flag === "not_applicable" || c.flag === "unavailable" ? `<p class="muted small">${esc(c.note || "")}</p>` : `
      <div class="row" style="gap:4px"><span class="big">${fmt(c.baseline_mean)}</span><span class="arrow">to</span>
        <span class="big">${fmt(c.current_mean)}</span><span class="muted small">${esc(c.units)}</span></div>
      <div class="muted small">${c.pct_change > 0 ? "+" : ""}${fmt(c.pct_change, 1)}% vs baseline; demo flag at ${fmt(c.threshold_pct, 0)}%;
        n=${c.baseline_n}/${c.current_n}</div>`}
      ${chart(c, e.series, s)}
      ${c.evidence_id ? `<button class="ghost small" data-src="${esc(s.evidence.find((x) => x.evidence_id === c.evidence_id)?.locator)}">Source rows</button>
        <code class="muted">${esc(c.evidence_id)}</code>` : ""}
    </div>`).join("");
  return `
  <div class="row spread"><div><h1>Evidence: ${esc(s.asset_id)}</h1>
    <p class="lede">Observed readings from the synthetic log. Shaded grey is the baseline hour; amber is the current window.</p></div>
    <button class="btn" data-go="work">Guided work</button></div>
  <div class="card"><div class="meta">
    <span>Dataset <b>${esc(e.dataset.name)}</b> <code>sha256 ${esc(s.dataset_hash.slice(0, 12))}</code></span>
    <span>Window <b>${esc(hhmm(s.window_start))} to ${esc(hhmm(s.window_end))} UTC</b></span>
    <span>Rows used <b>${s.rows.length}</b>${s.excluded_rows ? `, excluded ${s.excluded_rows}` : ""}</span>
    <span>Freshness ${pill(s.freshness)}</span><span>Retrieved in <b>${fmt(e.timing_ms, 1)} ms</b></span></div>
    ${s.limitations.length ? `<div class="callout" style="margin-top:10px">${s.limitations.map((l) => `<p>${esc(l)}</p>`).join("")}</div>` : ""}
  </div>
  <div class="grid g2">${cards}</div>
  <div class="card"><h2>Documents retrieved for this question</h2>
    ${e.sections.length ? `<table><tr><th>Section</th><th>Summary</th></tr>${e.sections.map((x) => `<tr class="click" data-src="${esc(x.evidence_id)}">
      <td class="mono">${esc(x.source_id)} ${esc(x.locator)}</td><td>${esc(x.summary.split(": ").slice(1).join(": "))}</td></tr>`).join("")}</table>`
      : `<p class="muted">No applicable document matched. For an asset with no pack, none exists.</p>`}
  </div>`;
}

function answer(p) {
  const cites = (ids) => ids.map((i) => `<button class="cite" data-src="${esc(srcFor(i))}" title="${esc(i)}">src</button>`).join("");
  const next = S.steps?.current ? S.steps.steps.find((x) => x.step_id === S.steps.current) : null;
  return `<div class="answer">
    <div><h3>Observed</h3><ul>${p.observations.map((o) => `<li>${esc(o.text)} ${cites(o.evidence_ids)}</li>`).join("") || "<li class='muted'>No readings.</li>"}</ul></div>
    <div><h3>Possible explanation</h3><ul>${p.possible_causes.map((c) => `<li><b>${esc(c.label)}</b> ${pill("needs_information", "possible")}<br><span class="small">${esc(c.why)}</span> ${cites(c.evidence_ids)}</li>`).join("")
      || "<li class='muted'>None the evidence points to.</li>"}</ul></div>
    <div><h3>Unknown</h3><ul>${p.unknowns.map((u) => `<li>${esc(u)}</li>`).join("")}${p.questions.map((q) => `<li><b>Ask:</b> ${esc(q)}</li>`).join("")}</ul></div>
    <div><h3>Next step</h3>${p.escalate ? `<div class="callout red"><p><b>Escalate.</b> ${esc(p.escalation_reason)}</p></div>` :
      next ? `<p><code>${esc(next.step_id)}</code> ${esc(next.text)} <button class="cite" data-src="${esc(next.citation)}">${esc(next.section)}</button></p>` : "<p class='muted'>All steps handled.</p>"}</div>
  </div>`;
}

function srcFor(id) {
  if (!id) return "";
  if (id.startsWith("doc-") || id.startsWith("job:")) return id;
  const e = S.evidence?.sensor?.evidence?.find((x) => x.evidence_id === id);
  return e ? e.locator : id;
}

function history(p) {
  const rec = p.recommended_history, oth = p.other_history;
  if (!rec.length && !oth.length) return "";
  const row = (h) => `<tr class="click" data-src="job:${esc(h.job_id)}"><td class="mono">${esc(h.job_id)}${h.seeded ? `<br><span class="muted">seeded demo</span>` : ""}</td>
    <td>${esc(h.asset_id)}</td><td>${esc(h.summary)}</td><td>${pill(h.review)}</td><td class="small">${esc(h.why)}</td></tr>`;
  return `<div class="card"><h2>Past jobs (LEDGER, retrieval-based memory)</h2>
    ${rec.length ? `<h3>Recommended experience</h3><table><tr><th>Job</th><th>Asset</th><th>What was done</th><th>Review</th><th>Why it matched</th></tr>${rec.map(row).join("")}</table>`
      : `<p class="muted small">No approved, reported-resolved case matches yet.</p>`}
    ${oth.length ? `<h3 style="margin-top:14px">History, not recommended</h3><table><tr><th>Job</th><th>Asset</th><th>What was done</th><th>Review</th><th>Why not</th></tr>${oth.map(row).join("")}</table>` : ""}
    <p class="muted small" style="margin:8px 0 0">The current procedure and WARDEN still govern every step. Approval is not proof a fix applies here.</p></div>`;
}

function work() {
  if (!S.jobId) return `<h1>Guided work</h1><div class="empty">Start a job in the Workspace first.</div>`;
  if (S.aiFailure) return `<h1>Guided work</h1><div class="callout red"><p><b>Local AI failed:</b> ${esc(S.aiFailure.error)}</p>
    <p>No guidance was produced. The Lab will not substitute scripted output without asking.</p></div>
    <div class="actions"><button class="btn alt" data-fallback="1">Use fixture mode instead (${esc(S.aiFailure.fallback_label)})</button></div>`;
  const p = S.guidance;
  if (!p) return `<h1>Guided work</h1><div class="empty">${S.evidence?.error ? "No guidance: the dataset was rejected." : "No guidance yet."}</div>`;
  const j = S.job.job, closed = j.state === "CLOSED";
  const st = S.steps?.steps || [];
  const sel = st.find((x) => x.step_id === (S.selStep || S.steps?.current)) || st[0];
  const prereqFacts = [...new Set(st.filter((x) => x.records).map((x) => x.records))];
  const recorded = new Set((S.steps?.prerequisites || []).map((r) => r.fact));
  return `
  <div class="row spread"><div><h1>Guided work: ${esc(j.asset_id)}</h1>
    <p class="lede">Guidance v${p.version} <span class="mono">${esc(p.proposal_id)}</span>. ${esc(p.mode_label)}.
    ${S.guidanceMs != null ? `Produced in ${fmt(S.guidanceMs, 1)} ms.` : ""}
    ${S.citeCheck ? `Citations resolved: ${S.citeCheck.ok}/${S.citeCheck.total}.` : ""}</p></div>
    ${!closed ? `<button class="btn alt" id="regen">New guidance version</button>` : ""}</div>
  ${answer(p)}
  ${history(p)}
  ${st.length ? `<div class="split" style="margin-top:16px">
    <div class="card"><h2>Steps</h2><ul class="steps">${st.map((x) => `
      <li data-step="${esc(x.step_id)}" class="${x.step_id === sel?.step_id ? "sel" : ""}">
        <span class="id">${esc(x.step_id)}</span><span class="t">${esc(x.text.length > 70 ? x.text.slice(0, 68) + "..." : x.text)}</span>
        <span class="st">${x.status === "pending" && x.policy.state !== "allowed" ? pill(x.policy.state, x.policy.state === "blocked" ? "blocked" : "needs info") : pill(x.status)}</span></li>`).join("")}</ul></div>
    <div>${sel ? stepCard(sel, closed) : ""}
      <div class="card"><h2>Prerequisites (operator attestations)</h2>
        <p class="attest">Recording one means you state it is true. The Lab cannot see isolation or PPE; WARDEN only checks the record exists
        for this job, asset and guidance version.</p>
        ${prereqFacts.map((f) => `<div class="row spread" style="padding:6px 0;border-bottom:1px solid var(--rule-2)">
          <span>${esc(FACT[f] || f)} <code class="muted">${esc(f)}</code></span>
          ${recorded.has(f) ? pill("recorded") : closed ? pill("pending", "not recorded") : `<button class="btn alt" data-prereq="${esc(f)}">Record attestation</button>`}</div>`).join("")}
      </div>
      ${closeCard(j, closed)}
    </div></div>` : `<div class="card" style="margin-top:16px">${closeCard(j, closed, true)}</div>`}`;
}

function stepCard(x, closed) {
  const pol = x.policy;
  const finished = ["done", "not_done", "unsuccessful", "skipped", "recorded"].includes(x.status);
  let acts = "";
  if (!closed && !finished) {
    if (x.kind === "prerequisite") {
      acts = `<button class="btn" data-prereq="${esc(x.records)}" data-pstep="${esc(x.step_id)}">Record attestation</button>
              <button class="btn alt" data-report="skipped">Skip</button>`;
    } else if (x.status === "acknowledged") {
      acts = `<select id="result" style="width:auto">${[["done", "Done"], ["unsuccessful", "Tried, unsuccessful"], ["not_done", "Not done"]].map(([v, l]) =>
              `<option value="${v}" ${S.form.result === v ? "selected" : ""}>${l}</option>`).join("")}</select>
              <input type="text" id="rnote" value="${esc(S.form.rnote)}" placeholder="What you did or found (optional)" style="flex:1;min-width:200px">
              <button class="btn" data-report="form">Report work</button>`;
    } else {
      acts = `<button class="btn" data-ack="1">I have read this</button><button class="btn alt" data-report="skipped">Skip</button>`;
    }
  }
  return `<div class="card stepcard">
    <div class="row spread"><span class="sid">${esc(x.step_id)} · ${esc(x.kind)} · <button class="ghost" data-src="${esc(x.citation)}">${esc(x.section)}</button></span>${pill(x.status)}</div>
    <div class="text">${esc(x.text)}</div>
    <div class="policy ${esc(pol.state)}"><div class="row spread"><b>WARDEN: ${esc(pol.state.replace(/_/g, " "))}</b><span class="rules">${esc(pol.policy)}</span></div>
      ${pol.rule_ids.length ? `<div class="rules">${pol.rule_ids.map((r, i) => `${esc(r)}: ${esc(pol.reasons[i] || "")}`).join("<br>")}</div>` : `<div class="small">All matching rules satisfied.</div>`}
      ${pol.missing.length ? `<div class="small">Needs: ${pol.missing.map((m) => `<code>${esc(m)}</code>`).join(", ")}</div>` : ""}</div>
    <div class="actions">${acts}</div>
    <p class="attest" style="margin:10px 0 0">${x.kind === "prerequisite" ? "Recording an attestation means you state it is true. The Lab cannot verify it."
      : `"I have read this" records that you read the instruction. It does not mean the work happened. "Report work" records what you say you did.`}</p></div>`;
}

function closeCard(j, closed, compact) {
  if (closed) return `<div class="card"><h2>Job closed</h2>${pill(j.outcome)} ${pill(j.review_status)}
    <p class="small muted" style="margin:8px 0 0">Reviewer status is separate from the operator's closure. Open the Review queue as the demo reviewer.</p></div>`;
  return `<div class="card"><h2>Close the job</h2>
    <div class="row small" style="margin-bottom:8px">${["REPORTED_RESOLVED", "UNRESOLVED", "ESCALATED"].map((o, i) =>
      `<label><input type="radio" name="outcome" value="${o}" ${o === (compact ? "ESCALATED" : S.form.outcome) ? "checked" : ""}> ${o.replace(/_/g, " ").toLowerCase()}</label>`).join("")}</div>
    <textarea id="summary" placeholder="What resolved it, or why it is unresolved (required for reported resolved)">${esc(S.form.summary)}</textarea>
    <div class="actions"><button class="btn" id="close">Close job</button></div></div>`;
}

function ledger() {
  const rows = S.jobs.map((v) => { const j = v.job; return `<tr class="click ${S.ledgerSel === j.job_id ? "sel" : ""}" data-ljob="${esc(j.job_id)}">
    <td class="mono">${esc(j.job_id)}${j.seeded ? "<br><span class='muted'>seeded demo</span>" : ""}</td><td>${esc(j.asset_id)}</td><td>${esc(j.scenario || "")}</td>
    <td>${pill(j.state)}</td><td>${j.outcome ? pill(j.outcome) : ""}</td><td>${pill(j.review_status)}</td><td>${esc(v.completeness.score)}</td></tr>`; }).join("");
  const ev = S.ledgerEvents;
  return `<h1>Work ledger</h1><p class="lede">Every job, and every event on it, in order. Events are append-only; a correction is a new event that points at the old one.</p>
  <div class="card"><table><tr><th>Job</th><th>Asset</th><th>Scenario</th><th>State</th><th>Operator outcome</th><th>Review</th><th>Record</th></tr>${rows}</table></div>
  ${S.ledgerSel ? `<div class="card"><div class="row spread"><h2>Events: ${esc(S.ledgerSel)}</h2>
    <div class="row"><a class="btn alt" href="/api/jobs/${esc(S.ledgerSel)}/export?format=markdown" target="_blank" rel="noopener">Markdown export</a>
    <a class="btn alt" href="/api/jobs/${esc(S.ledgerSel)}/export?format=json" target="_blank" rel="noopener">JSON export</a></div></div>
    <ul class="timeline">${ev.map((e) => `<li class="${/REJECTED|FAILED/.test(e.type) ? "rej" : ""}"><span class="muted mono">${e.seq}</span>
      <span class="muted mono">${esc(e.created_at.slice(11, 19))}</span><span><span class="ty">${esc(e.type)}</span> ${esc(evDetail(e))}
      ${e.corrects ? `<span class="muted">(corrects ${esc(e.corrects)})</span>` : ""} <code class="muted">${esc(e.event_id)}</code></span></li>`).join("")}</ul>
    <div class="row" style="margin-top:12px"><input type="text" id="note" value="${esc(S.form.note)}" placeholder="Add a note, or a correction to an event">
      <select id="corr" style="width:auto"><option value="">as a new note</option>${ev.map((e) => `<option value="${esc(e.event_id)}">correct #${e.seq} ${esc(e.type)}</option>`).join("")}</select>
      <button class="btn alt" id="addnote">Append</button></div></div>` : ""}`;
}

function evDetail(e) {
  const p = e.payload;
  if (e.type === "STEP_REPORTED") return `${p.step_id}: ${p.result}${p.note ? `, "${p.note}"` : ""}`;
  if (/REJECTED/.test(e.type) && p.rule_ids) return `${p.step_id}: ${p.rule_ids.join(", ")} (${(p.reasons || []).join("; ")})`;
  if (e.type === "EVIDENCE_RETRIEVED") return `${p.dataset} ${p.freshness}, ${Object.entries(p.flags).map(([k, v]) => `${LABEL[k]} ${v}`).join(", ")}`;
  if (e.type === "PROPOSAL_CREATED") return `v${p.version} ${p.mode}: ${p.steps.join(" ") || "no steps"}`;
  if (e.type === "POLICY_EVALUATED") return Object.entries(p.decisions).filter(([, d]) => d.state !== "allowed").map(([k, d]) => `${k} ${d.state}`).join(", ") || "all allowed";
  return p.step_id || p.fact || (p.outcome ? `${p.outcome} ${p.summary || ""}` : "") || p.text || p.reason || p.question || p.error || p.label || "";
}

function review() {
  const pending = S.jobs.filter((v) => v.job.review_status === "PENDING");
  const approved = S.jobs.filter((v) => v.job.review_status === "APPROVED" && !v.job.seeded);
  const sel = S.jobs.find((v) => v.job.job_id === S.reviewSel);
  const isRev = S.role === "reviewer";
  const list = (xs) => xs.map((v) => `<tr class="click ${S.reviewSel === v.job.job_id ? "sel" : ""}" data-rjob="${esc(v.job.job_id)}"><td class="mono">${esc(v.job.job_id)}</td>
    <td>${esc(v.job.asset_id)}</td><td>${pill(v.job.outcome)}</td><td>${esc(v.completeness.score)}</td></tr>`).join("");
  return `<h1>Review queue</h1>
  <div class="callout ${isRev ? "blue" : ""}"><p><b>Demo roles, not authenticated.</b> Switching to "Reviewer" is a simulation of a senior's sign-off,
    not a secure approval. ${isRev ? "You are acting as the demo reviewer." : "Switch to Reviewer (top right) to decide."}</p></div>
  <div class="grid g2" style="margin-top:16px">
    <div class="card"><h2>Waiting for review</h2>${pending.length ? `<table><tr><th>Job</th><th>Asset</th><th>Outcome</th><th>Record</th></tr>${list(pending)}</table>` : `<p class="muted">Nothing waiting.</p>`}</div>
    <div class="card"><h2>Approved for reuse</h2>${approved.length ? `<table><tr><th>Job</th><th>Asset</th><th>Outcome</th><th>Record</th></tr>${list(approved)}</table>` : `<p class="muted">None yet.</p>`}</div>
  </div>
  ${sel ? `<div class="card"><h2>${esc(sel.job.job_id)}</h2>${jobSummary(sel)}
    <p class="small">Record checks: ${Object.entries(sel.completeness.checks).map(([k, v]) => `${v ? "yes" : "<b>no</b>"} ${esc(k.replace(/_/g, " "))}`).join(" · ")}</p>
    <div class="row"><button class="ghost" data-src="job:${esc(sel.job.job_id)}">Open the full record</button></div>
    ${isRev ? `<label class="f" for="reason" style="margin-top:10px">Reason (required)</label><input type="text" id="reason" value="${esc(S.form.reason)}" placeholder="Why you approve, reject or revoke">
    <div class="actions">${sel.job.review_status === "PENDING" ? `<button class="btn" data-review="approve">Approve for reuse</button><button class="btn danger" data-review="reject">Reject</button>` : ""}
      ${sel.job.review_status === "APPROVED" ? `<button class="btn danger" data-review="revoke">Revoke</button>` : ""}</div>` : ""}</div>` : ""}`;
}

function dev() {
  const d = S.dev;
  if (!d) return `<h1>Developer</h1><div class="empty">Loading...</div>`;
  const j = (o) => `<pre>${esc(JSON.stringify(o, null, 2))}</pre>`;
  return `<h1>Developer panel</h1><p class="lede">What each recommendation was based on: fixtures, versions, contracts, policy results and real timings.</p>
  <div class="grid g4">
    <div class="card"><h2>Mode</h2><p>${esc(d.mode_label)}</p></div>
    <div class="card"><h2>Policy tests</h2>${Object.entries(d.policy_tests).map(([k, v]) => `<p><b class="big">${v.passed}/${v.executed}</b><br><span class="small">${esc(k)}: violations blocked ${esc(v.violations_blocked)}, false blocks ${esc(v.false_blocks)}</span></p>`).join("")}</div>
    <div class="card"><h2>Startup</h2><p class="big">${fmt(d.startup_ms, 0)} ms</p><p class="small muted">${esc(d.db)}</p></div>
    <div class="card"><h2>Packs</h2>${Object.values(d.packs).map((p) => `<p class="small">${esc(p.label)}<br>${p.files} files, sha256 verified</p>`).join("")}</div>
  </div>
  <div class="grid g2">
    <div class="card"><h2>Documents</h2><table><tr><th>Doc</th><th>Version</th><th>sha256</th><th>Source</th></tr>${d.documents.map((x) => `<tr><td class="mono">${esc(x.doc_id)}</td><td>${esc(x.version)}</td><td class="mono">${esc(x.sha256)}</td><td>${esc(x.source)}</td></tr>`).join("")}</table></div>
    <div class="card"><h2>Backend calls (server timings)</h2><table><tr><th>At</th><th>Call</th><th>ms</th><th>Detail</th></tr>${d.calls.map((c) => `<tr><td class="mono">${esc(c.at)}</td><td>${esc(c.call)}</td><td class="mono">${fmt(c.ms, 1)}</td><td class="small">${esc(Object.entries(c).filter(([k]) => !["at", "call", "ms"].includes(k)).map(([k, v]) => `${k}=${v}`).join(" "))}</td></tr>`).join("")}</table></div>
  </div>
  <div class="card"><h2>Browser requests</h2><table><tr><th>At</th><th>Request</th><th>Status</th><th>ms</th></tr>${S.calls.slice(0, 15).map((c) => `<tr><td class="mono">${esc(c.at)}</td><td class="mono">${esc(c.method)} ${esc(c.path)}</td><td>${c.status}</td><td class="mono">${c.ms}</td></tr>`).join("")}</table></div>
  <div class="grid g2">
    <div class="card"><h2>Evidence bundle (current job)</h2>${S.evidence?.sensor ? j(S.evidence.sensor.evidence) : "<p class='muted'>none</p>"}</div>
    <div class="card"><h2>Validated guidance</h2>${S.guidance ? j(S.guidance) : "<p class='muted'>none</p>"}</div>
    <div class="card"><h2>Policy decisions</h2>${S.steps ? j(Object.fromEntries(S.steps.steps.map((s) => [s.step_id, s.policy]))) : "<p class='muted'>none</p>"}</div>
    <div class="card"><h2>Last error</h2>${S.lastError ? j(S.lastError) : "<p class='muted'>none</p>"}</div>
    <div class="card"><h2>Scenario settings</h2>${j(d.scenario_settings)}</div>
  </div>`;
}

// ---------- source drawer ----------
function mdLite(text) {
  return text.split(/\n{2,}/).map((b) => {
    const lines = b.split("\n");
    if (lines.every((l) => l.startsWith("- "))) return `<ul>${lines.map((l) => `<li>${inline(l.slice(2))}</li>`).join("")}</ul>`;
    return `<p>${inline(b.replace(/\n/g, " "))}</p>`;
  }).join("");
}
const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`(.+?)`/g, "<code>$1</code>");

async function openSource(ref) {
  if (!ref) return;
  const d = $("#drawer"), body = $("#drawer-body");
  d.classList.add("open"); d.setAttribute("aria-hidden", "false");
  body.innerHTML = "Loading...";
  try {
    const s = await api("GET", `/api/sources?ref=${encodeURIComponent(ref)}`);
    if (s.kind === "document_section") {
      $("#drawer-title").textContent = `${s.doc_id}-${s.version} ${s.section_id}`;
      body.innerHTML = `<div class="callout"><p>${esc(s.label)}</p></div><h3 style="margin-top:12px">${esc(s.title)}</h3>
        <div class="doc">${mdLite(s.text)}</div><p class="muted small">sha256 ${esc(s.sha256.slice(0, 16))} · ${esc(s.source)}</p>`;
    } else if (s.kind === "dataset_rows") {
      const col = Object.keys(s.rows[0] || {}).find((k) => LABEL[k]);
      $("#drawer-title").textContent = `Rows: ${ref.split("#")[0]}`;
      body.innerHTML = `<div class="callout"><p>${esc(s.label)}</p></div><p class="small mono">${esc(ref)}</p>
        <table><tr><th>Row</th><th>Time UTC</th><th>${esc(LABEL[col] || col)}</th><th>Quality</th></tr>${s.rows.map((r) =>
        `<tr><td class="mono">${r.row}</td><td class="mono">${esc(r.timestamp_utc.slice(11, 16))}</td><td class="mono">${r[col] ?? "empty"}</td><td>${esc(r.quality)}</td></tr>`).join("")}</table>`;
    } else {
      $("#drawer-title").textContent = `Job ${s.job.job_id}`;
      body.innerHTML = `${s.job.seeded ? `<div class="callout"><p>Seeded demo history (fictional).</p></div>` : ""}${jobSummary(s)}
        <ul class="timeline" style="margin-top:10px">${s.events.map((e) => `<li><span class="muted mono">${e.seq}</span><span class="muted mono">${esc(e.created_at.slice(11, 19))}</span>
        <span><span class="ty">${esc(e.type)}</span> ${esc(evDetail(e))}</span></li>`).join("")}</ul>`;
    }
  } catch (e) { body.innerHTML = `<div class="callout red"><p>${esc(e.message)}</p></div>`; }
}

// ---------- events ----------
function bind() {
  const on = (sel, ev, fn) => document.querySelectorAll(sel).forEach((el) => el.addEventListener(ev, fn));
  on("#main [data-src]", "click", (e) => { e.stopPropagation(); openSource(e.currentTarget.dataset.src); });
  on("[data-go]", "click", (e) => go(e.currentTarget.dataset.go));
  on("#sc", "change", (e) => { S.scenario = e.target.value; });
  on("#as", "change", (e) => { S.asset = e.target.value; });
  on("#q", "input", (e) => { S.question = e.target.value; });
  for (const id of ["summary", "rnote", "reason", "note", "result"]) on("#" + id, "input", (e) => { S.form[id] = e.target.value; });
  on("#result", "change", (e) => { S.form.result = e.target.value; });
  on("input[name=outcome]", "change", (e) => { S.form.outcome = e.target.value; });
  on("input[name=mode]", "change", (e) => { S.mode = e.target.value; });
  on("[data-ex]", "click", (e) => { const [a, q] = EXAMPLES[+e.currentTarget.dataset.ex]; S.asset = a; S.question = q; render(); });
  on("#start", "click", () => run(startJob));
  on("[data-fallback]", "click", () => run(async () => { S.aiFailure = null; await askGuidance("fixture"); }));
  on("#regen", "click", () => run(async () => { await askGuidance(S.mode); toast("New guidance version. Earlier attestations do not carry over."); }));
  on("[data-step]", "click", (e) => { S.selStep = e.currentTarget.dataset.step; render(); });
  const pid = () => S.steps.proposal_id, sid = () => S.selStep || S.steps.current;
  on("[data-ack]", "click", () => run(async () => {
    try { await api("POST", `/api/jobs/${S.jobId}/steps/${sid()}/acknowledge`, { proposal_id: pid() }, { idem: key() }); }
    finally { await refreshSteps(); }
  }));
  on("[data-report]", "click", (e) => run(async () => {
    const form = e.currentTarget.dataset.report === "form";
    const body = { proposal_id: pid(), result: form ? $("#result").value : "skipped", note: form ? $("#rnote").value : "" };
    const step = sid();
    try { await api("POST", `/api/jobs/${S.jobId}/steps/${step}/report`, body, { idem: key() }); S.form.rnote = ""; S.form.result = "done"; }
    finally { await refreshSteps(); }
    if (S.steps.current) S.selStep = S.steps.current;
  }));
  on("[data-prereq]", "click", (e) => run(async () => {
    const f = e.currentTarget.dataset.prereq, st = e.currentTarget.dataset.pstep || null;
    await api("POST", `/api/jobs/${S.jobId}/prerequisites`, { proposal_id: pid(), fact: f, step_id: st }, { idem: key() });
    await refreshSteps(); toast(`Attestation recorded: ${f}. WARDEN re-checked every step.`);
    if (st && S.steps.current) S.selStep = S.steps.current;
  }));
  on("#close", "click", () => run(async () => {
    const outcome = document.querySelector("input[name=outcome]:checked").value;
    await api("POST", `/api/jobs/${S.jobId}/close`, { outcome, summary: $("#summary").value }, { idem: key() });
    S.form.summary = "";
    await refreshSteps().catch(() => {}); S.job = await api("GET", `/api/jobs/${S.jobId}`);
    toast("Job closed. It now waits for a demo reviewer.");
  }));
  on("[data-ljob]", "click", (e) => run(async () => { S.ledgerSel = e.currentTarget.dataset.ljob; S.ledgerEvents = await api("GET", `/api/jobs/${S.ledgerSel}/events`); }));
  on("#addnote", "click", () => run(async () => {
    const text = $("#note").value, corrects = $("#corr").value || null;
    await api("POST", `/api/jobs/${S.ledgerSel}/notes`, { text, corrects }, { idem: key() });
    S.form.note = "";
    S.ledgerEvents = await api("GET", `/api/jobs/${S.ledgerSel}/events`); S.jobs = await api("GET", "/api/jobs");
  }));
  on("[data-rjob]", "click", (e) => { S.reviewSel = e.currentTarget.dataset.rjob; render(); });
  on("[data-review]", "click", (e) => run(async () => {
    const decision = e.currentTarget.dataset.review;
    await api("POST", `/api/jobs/${S.reviewSel}/reviews`, { decision, reason: $("#reason").value }, { idem: key() });
    S.form.reason = "";
    S.jobs = await api("GET", "/api/jobs"); toast(`Review recorded: ${decision}.`);
  }));
}

async function go(view) {
  S.view = view;
  if (view === "ledger" || view === "review") S.jobs = await api("GET", "/api/jobs").catch(() => S.jobs);
  if (view === "ledger" && S.ledgerSel) S.ledgerEvents = await api("GET", `/api/jobs/${S.ledgerSel}/events`).catch(() => []);
  if (view === "dev") S.dev = await api("GET", "/api/developer").catch(() => null);
  render(); $("#main").focus({ preventScroll: true }); window.scrollTo({ top: 0 });
}

document.querySelectorAll(".nav [data-view]").forEach((b) => b.addEventListener("click", () => go(b.dataset.view)));
document.querySelectorAll(".roles .seg").forEach((b) => b.addEventListener("click", () => { S.role = b.dataset.role; render(); toast(`Demo role: ${S.role}. Not authenticated.`); }));
$("#drawer-close").addEventListener("click", () => { $("#drawer").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true"); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#drawer-close").click(); });

(async function init() {
  try {
    [S.meta, S.scenarios] = await Promise.all([api("GET", "/api/meta"), api("GET", "/api/scenarios")]);
    S.mode = S.meta.mode;
    S.question = EXAMPLES[0][1];
  } catch (e) { toast("Backend not reachable: " + e.message, true); }
  render();
})();
