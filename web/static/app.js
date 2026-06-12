const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const state = {
  downloadSymbols: new Set(),
  gapSymbols: new Set(),
  jobStatuses: new Map(),
  jobsPollTimer: null,
};

// -- utils --------------------------------------------------------------------

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 3200);
}

function fmt(n) {
  return Number(n || 0).toLocaleString();
}

function fmtEta(sec) {
  sec = Math.max(0, Number(sec) || 0);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

function statusBadge(status) {
  return `<span class="status-badge status-${status}">${status}</span>`;
}

function formatDateValue(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function defaultDates() {
  const end = new Date();
  end.setDate(end.getDate() - 2);
  const start = new Date(end);
  start.setMonth(start.getMonth() - 1);
  return {
    start: formatDateValue(start),
    end: formatDateValue(end),
  };
}

function readDateInput(selector, label) {
  const raw = $(selector).value;
  if (!raw) {
    throw new Error(`${label} is required`);
  }
  return raw;
}

function initDateInputs() {
  const max = formatDateValue(new Date());
  ["dl-start", "dl-end", "ex-start", "ex-end", "gp-start", "gp-end"].forEach((id, i) => {
    const el = $(`#${id}`);
    if (!el) return;
    el.setAttribute("lang", "en-CA");
    el.min = "1970-01-01";
    el.max = max;
    el.value = i % 2 === 0 ? dates.start : dates.end;
  });
}

function formatApiError(data, statusText) {
  const detail = data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return data?.error || statusText || "Request failed";
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...opts.headers },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(formatApiError(data, res.statusText));
  return data;
}

// -- tabs ---------------------------------------------------------------------

const TAB_STORAGE_KEY = "activeTab";
const VALID_TABS = new Set(["download", "export", "gaps", "library", "jobs"]);

function switchTab(tabName, { save = true } = {}) {
  if (!VALID_TABS.has(tabName)) tabName = "download";
  $$(".tab").forEach((t) => t.classList.remove("active"));
  $$(".panel").forEach((p) => p.classList.remove("active"));
  const tab = $(`.tab[data-tab="${tabName}"]`);
  if (!tab) return;
  tab.classList.add("active");
  $(`#panel-${tabName}`).classList.add("active");
  if (tabName === "library") loadLibrary();
  if (tabName === "export") loadExports();
  if (tabName === "jobs") loadJobs();
  if (save) localStorage.setItem(TAB_STORAGE_KEY, tabName);
}

$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => switchTab(tab.dataset.tab));
});

// -- instrument search --------------------------------------------------------

function setupSearch(inputId, suggestionsId, onPick, single = false) {
  const input = $(inputId);
  const box = $(suggestionsId);
  let timer = null;

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 1) {
      box.classList.remove("open");
      return;
    }
    timer = setTimeout(async () => {
      try {
        const { results } = await api(`/api/instruments/search?q=${encodeURIComponent(q)}&limit=12`);
        if (!results.length) {
          box.innerHTML = `<div class="suggestion"><span>No matches</span></div>`;
        } else {
          box.innerHTML = results
            .map(
              (r) =>
                `<div class="suggestion" data-symbol="${r.symbol}">
                  <strong>${r.symbol}</strong> ${r.name}
                  <span>${r.description || ""}</span>
                </div>`,
            )
            .join("");
        }
        box.classList.add("open");
        box.querySelectorAll(".suggestion[data-symbol]").forEach((el) => {
          el.addEventListener("click", () => {
            onPick(el.dataset.symbol, results.find((r) => r.symbol === el.dataset.symbol));
            input.value = single ? el.dataset.symbol : "";
            box.classList.remove("open");
          });
        });
      } catch (e) {
        toast(e.message);
      }
    }, 220);
  });

  document.addEventListener("click", (e) => {
    if (!input.contains(e.target) && !box.contains(e.target)) box.classList.remove("open");
  });
}

function renderDownloadChips() {
  const wrap = $("#dl-chips");
  wrap.innerHTML = [...state.downloadSymbols]
    .map(
      (sym) =>
        `<span class="chip">${sym}<button type="button" data-remove="${sym}" aria-label="Remove">×</button></span>`,
    )
    .join("");
  wrap.querySelectorAll("[data-remove]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.downloadSymbols.delete(btn.dataset.remove);
      renderDownloadChips();
    });
  });
}

setupSearch("#dl-search", "#dl-suggestions", (symbol) => {
  state.downloadSymbols.add(symbol);
  renderDownloadChips();
});

setupSearch(
  "#ex-search",
  "#ex-suggestions",
  (symbol) => {
    $("#ex-symbol").value = symbol;
    $("#ex-search").value = symbol;
  },
  true,
);

function renderGapChips() {
  const wrap = $("#gp-chips");
  wrap.innerHTML = [...state.gapSymbols]
    .map(
      (sym) =>
        `<span class="chip">${sym}<button type="button" data-gp-remove="${sym}" aria-label="Remove">×</button></span>`,
    )
    .join("");
  wrap.querySelectorAll("[data-gp-remove]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.gapSymbols.delete(btn.dataset.gpRemove);
      renderGapChips();
    });
  });
}

setupSearch("#gp-search", "#gp-suggestions", (symbol) => {
  state.gapSymbols.add(symbol);
  renderGapChips();
});

// -- dates defaults -----------------------------------------------------------

const dates = defaultDates();
initDateInputs();

function toggleDateFields(checkboxId, datesId) {
  $(checkboxId).addEventListener("change", (e) => {
    $(datesId).style.opacity = e.target.checked ? "0.4" : "1";
    $(datesId).style.pointerEvents = e.target.checked ? "none" : "auto";
  });
}

toggleDateFields("#gp-all", "#gp-dates");
toggleDateFields("#ex-all", "#ex-dates");

// -- job updates ----------------------------------------------------------------

function renderJobProgress(job) {
  const p = job.progress || {};
  const pct = p.percent ?? 0;
  let html;

  if (job.kind === "export") {
    html = `
    <div class="progress-meta">
      <span>${p.message || "Exporting"}</span>
      <span>${pct}%</span>
    </div>
    <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
    <div class="progress-meta">
      <span>${fmt(p.done)}/${fmt(p.total)} hours scanned · ${fmt(p.hours_with_data)} with data</span>
      <span>${fmt(p.rows)} ticks written</span>
    </div>`;
  } else {
    html = `
    <div class="progress-meta">
      <span>${p.message || job.kind}</span>
      <span>${pct}%</span>
    </div>
    <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
    <div class="progress-meta">
      <span>${fmt(p.done)}/${fmt(p.total)} hours · ok ${fmt(p.completed)} · empty ${fmt(p.empty)} · failed ${fmt(p.failed)}</span>
      <span>${fmt(p.ticks)} ticks · ${p.rate || 0} h/s · ETA ${fmtEta(p.eta_seconds)}</span>
    </div>`;
    if (p.throttle) {
      const t = p.throttle;
      const rl = t.rate_limit_hits ? ` · ${t.rate_limit_hits}×429 (30s)` : "";
      html += `
    <div class="progress-meta throttle-meta">
      <span>Throttle: <strong>${t.state}</strong></span>
      <span>inflight ${t.inflight}/${t.limit} · ceiling ${t.ceiling}${rl}</span>
    </div>`;
    }
  }

  if (p.symbols && Object.keys(p.symbols).length) {
    html += `<div class="symbol-grid">${Object.entries(p.symbols)
      .map(
        ([sym, s]) =>
          `<div class="symbol-stat"><strong>${sym}</strong>ok ${s.completed} · empty ${s.empty} · fail ${s.failed}<br>${fmt(s.ticks)} ticks</div>`,
      )
      .join("")}</div>`;
  }
  return html;
}

function isJobActive(status) {
  return status === "pending" || status === "running";
}

async function cancelJob(jobId) {
  try {
    await api(`/api/jobs/${jobId}/cancel`, { method: "POST" });
    toast("Job cancelled");
    refreshJobs();
  } catch (e) {
    toast(e.message);
  }
}

function renderJobCard(job) {
  const div = document.createElement("div");
  div.className = "job-card";
  div.id = `job-${job.id}`;
  const params = job.params || {};
  const title =
    job.kind === "download"
      ? `Download · ${(params.symbols || []).join(", ")}`
      : job.kind === "export"
        ? `Export · ${params.symbol}${params.export_all ? " (all recorded)" : ""}`
        : `Gaps · ${(params.symbols || (params.symbol ? [params.symbol] : [])).join(", ")}`;

  const cancelBtn = isJobActive(job.status)
    ? `<button type="button" class="job-cancel" data-cancel="${job.id}" aria-label="Cancel job">×</button>`
    : "";

  let body = renderJobProgress(job);
  if (job.status === "completed" && job.result) {
    const r = job.result;
    if (job.kind === "download") {
      body += `<p class="hint">Done: ${fmt(r.completed)} with data, ${fmt(r.empty)} empty, ${fmt(r.failed)} failed, ${fmt(r.ticks)} ticks.</p>`;
    } else if (job.kind === "export") {
      const range = r.range ? `<br>${r.range}` : "";
      body += `<p class="hint">Exported ${fmt(r.rows)} ticks from ${fmt(r.hours_with_data)} hours${range}<br><a href="/api/exports/file?path=${encodeURIComponent(r.path)}">${r.filename || r.path}</a></p>`;
    } else if (job.kind === "gaps") {
      if (params.repair && r.ticks !== undefined) {
        body += `<p class="hint">Done: ${fmt(r.completed)} with data, ${fmt(r.empty)} empty, ${fmt(r.failed)} failed, ${fmt(r.ticks)} ticks.</p>`;
      } else if (r.complete) {
        body += `<p class="hint">Dataset complete.</p>`;
      } else {
        body += `<p class="hint">Gaps: ${r.gap_count ?? "?"} hour(s) missing or failed.</p>`;
      }
    }
  }
  if (job.status === "failed") {
    body += `<p class="hint" style="color:var(--danger)">${job.error}</p>`;
  }
  if (job.status === "cancelled") {
    body += `<p class="hint" style="color:var(--text-muted)">Stopped by user.</p>`;
  }

  div.innerHTML = `<div class="job-head"><h3>${title} ${statusBadge(job.status)}</h3>${cancelBtn}</div>${body}`;
  const btn = div.querySelector("[data-cancel]");
  if (btn) btn.addEventListener("click", () => cancelJob(job.id));
  return div;
}

function renderJobs(jobsArr) {
  const list = $("#jobs-list");
  if (!jobsArr.length) {
    list.innerHTML = '<p class="empty">No jobs yet.</p>';
    return false;
  }

  let anyActive = false;
  const frag = document.createDocumentFragment();
  jobsArr.forEach((job) => {
    const prev = state.jobStatuses.get(job.id);
    const finished = job.status === "completed" || job.status === "failed" || job.status === "cancelled";
    if (prev && prev !== job.status && finished) {
      if (job.status === "completed") toast("Job completed");
      else if (job.status === "failed") toast("Job failed");
      loadLibrary();
      loadExports();
    }
    state.jobStatuses.set(job.id, job.status);
    if (!finished) anyActive = true;
    frag.appendChild(renderJobCard(job));
  });
  list.replaceChildren(frag);
  return anyActive;
}

async function refreshJobs() {
  clearTimeout(state.jobsPollTimer);
  try {
    const { jobs } = await api("/api/jobs");
    const anyActive = renderJobs(jobs);
    if (anyActive) {
      state.jobsPollTimer = setTimeout(refreshJobs, 600);
    }
  } catch (e) {
    $("#jobs-list").innerHTML = `<p class="empty">${e.message}</p>`;
  }
}

function prependJob(job) {
  const list = $("#jobs-list");
  if (list.querySelector(".empty")) list.innerHTML = "";
  list.prepend(renderJobCard(job));
  state.jobStatuses.set(job.id, job.status);
  switchTab("jobs");
  refreshJobs();
}

// -- download -----------------------------------------------------------------

$("#dl-start-btn").addEventListener("click", async () => {
  const symbols = [...state.downloadSymbols];
  if (!symbols.length) return toast("Add at least one symbol");
  let start;
  let end;
  try {
    start = readDateInput("#dl-start", "Start date");
    end = readDateInput("#dl-end", "End date");
  } catch (e) {
    return toast(e.message);
  }
  const body = {
    symbols,
    start,
    end,
    workers: Number($("#dl-workers").value) || 10,
    force: $("#dl-force").checked,
  };
  try {
    const job = await api("/api/download", { method: "POST", body: JSON.stringify(body) });
    toast(`Download started (${symbols.length} symbol${symbols.length > 1 ? "s" : ""})`);
    prependJob(job);
  } catch (e) {
    toast(e.message);
  }
});

// -- export -------------------------------------------------------------------

$("#ex-start-btn").addEventListener("click", async () => {
  const symbol = $("#ex-symbol").value || $("#ex-search").value.trim();
  if (!symbol) return toast("Select a symbol");
  const exportAll = $("#ex-all").checked;
  const body = { symbol, export_all: exportAll };
  if (!exportAll) {
    try {
      body.start = readDateInput("#ex-start", "Start date");
      body.end = readDateInput("#ex-end", "End date");
    } catch (e) {
      return toast(e.message);
    }
  }
  try {
    const job = await api("/api/export", { method: "POST", body: JSON.stringify(body) });
    toast(exportAll ? "Export all started" : "Export started");
    prependJob(job);
  } catch (e) {
    toast(e.message);
  }
});

async function deleteExport(path, filename) {
  if (!confirm(`Delete ${filename}?`)) return;
  try {
    await api(`/api/exports/file?path=${encodeURIComponent(path)}`, { method: "DELETE" });
    toast("Export deleted");
    loadExports();
  } catch (e) {
    toast(e.message);
  }
}

async function loadExports() {
  try {
    const { exports } = await api("/api/exports");
    const el = $("#export-list");
    if (!exports.length) {
      el.innerHTML = '<p class="empty">No exports yet.</p>';
      return;
    }
    el.innerHTML = `<table><thead><tr><th>File</th><th>Size</th><th></th></tr></thead><tbody>${exports
      .map(
        (f) =>
          `<tr><td>${f.filename}</td><td>${(f.size / 1024 / 1024).toFixed(2)} MB</td>
          <td class="export-actions">
            <a class="btn" href="/api/exports/file?path=${encodeURIComponent(f.path)}">Download</a>
            <button type="button" class="job-cancel lib-delete" data-delete-export="${f.path}" aria-label="Delete ${f.filename}">×</button>
          </td></tr>`,
      )
      .join("")}</tbody></table>`;
    el.querySelectorAll("[data-delete-export]").forEach((btn) => {
      const path = btn.dataset.deleteExport;
      const filename = exports.find((f) => f.path === path)?.filename || path;
      btn.addEventListener("click", () => deleteExport(path, filename));
    });
  } catch (e) {
    $("#export-list").innerHTML = `<p class="empty">${e.message}</p>`;
  }
}

// -- gaps ---------------------------------------------------------------------

function gapScanBody(repair) {
  const symbols = [...state.gapSymbols];
  if (!symbols.length) throw new Error("Add at least one symbol");
  const all = $("#gp-all").checked;
  const body = { symbols, all, repair };
  if (!all) {
    body.start = readDateInput("#gp-start", "Start date");
    body.end = readDateInput("#gp-end", "End date");
  }
  if (repair) body.workers = Number($("#dl-workers").value) || 10;
  return body;
}

function renderGapPreviewResult(r) {
  const blocks = (r.results || []).map((item) => {
    if (item.message) {
      return `<div class="progress-block"><p><strong>${item.symbol}</strong> · ${item.message}</p></div>`;
    }
    return `<div class="progress-block">
      <p><strong>${item.symbol}</strong> · ${item.range}</p>
      <p class="hint">${fmt(item.total_hours)} hours · ${fmt(item.completed)} with data · ${fmt(item.empty)} empty · ${fmt(item.failed)} failed · ${fmt(item.missing)} missing</p>
      ${
        item.complete
          ? '<p style="color:var(--success)">Dataset is complete.</p>'
          : `<p style="color:var(--warning)">${item.gap_count} gap hour(s)</p>
             <p class="hint">${(item.gap_hours || []).join(", ")}</p>`
      }
    </div>`;
  });
  return blocks.join("");
}

async function runGapsPreview() {
  let body;
  try {
    body = gapScanBody(false);
  } catch (e) {
    return toast(e.message);
  }
  try {
    const r = await api("/api/gaps/preview", { method: "POST", body: JSON.stringify(body) });
    $("#gp-result").innerHTML = renderGapPreviewResult(r);
  } catch (e) {
    toast(e.message);
  }
}

$("#gp-scan-btn").addEventListener("click", runGapsPreview);

$("#gp-add-library-btn").addEventListener("click", async () => {
  try {
    const { instruments } = await api("/api/status");
    if (!instruments.length) return toast("Library is empty");
    instruments.forEach((i) => state.gapSymbols.add(i.symbol));
    renderGapChips();
    toast(`Added ${instruments.length} symbol${instruments.length > 1 ? "s" : ""} from library`);
  } catch (e) {
    toast(e.message);
  }
});

$("#gp-repair-btn").addEventListener("click", async () => {
  let body;
  try {
    body = gapScanBody(true);
  } catch (e) {
    return toast(e.message);
  }
  try {
    const job = await api("/api/gaps", { method: "POST", body: JSON.stringify(body) });
    toast("Repair job started");
    prependJob(job);
  } catch (e) {
    toast(e.message);
  }
});

// -- library ------------------------------------------------------------------

async function deleteLibrarySymbol(symbol) {
  if (!confirm(`Delete all stored data for ${symbol}? This cannot be undone.`)) return;
  try {
    await api(`/api/status/${encodeURIComponent(symbol)}`, { method: "DELETE" });
    state.downloadSymbols.delete(symbol);
    state.gapSymbols.delete(symbol);
    renderDownloadChips();
    renderGapChips();
    toast(`${symbol} removed`);
    loadLibrary();
    loadExports();
  } catch (e) {
    toast(e.message);
  }
}

async function loadLibrary() {
  try {
    const { instruments } = await api("/api/status");
    const el = $("#library-table");
    if (!instruments.length) {
      el.innerHTML = '<p class="empty">No data stored yet. Start a download.</p>';
      return;
    }
    el.innerHTML = `<table><thead><tr><th>Symbol</th><th>Range (UTC)</th><th>Completed</th><th>Empty</th><th>Ticks</th><th></th></tr></thead><tbody>${instruments
      .map((i) => {
        const c = i.by_status?.completed || { hours: 0, ticks: 0 };
        const e = i.by_status?.empty || { hours: 0 };
        return `<tr>
          <td><strong>${i.symbol}</strong><br><span class="hint">${i.name}</span></td>
          <td class="hint">${i.first_hour || "—"}<br>${i.last_hour || ""}</td>
          <td>${fmt(c.hours)}</td>
          <td>${fmt(e.hours)}</td>
          <td>${fmt(c.ticks)}</td>
          <td class="lib-actions"><button type="button" class="job-cancel lib-delete" data-delete-symbol="${i.symbol}" aria-label="Delete ${i.symbol}">×</button></td>
        </tr>`;
      })
      .join("")}</tbody></table>`;
    el.querySelectorAll("[data-delete-symbol]").forEach((btn) => {
      btn.addEventListener("click", () => deleteLibrarySymbol(btn.dataset.deleteSymbol));
    });
  } catch (e) {
    $("#library-table").innerHTML = `<p class="empty">${e.message}</p>`;
  }
}

// -- jobs list ----------------------------------------------------------------

const loadJobs = refreshJobs;

$("#lib-refresh").addEventListener("click", loadLibrary);
$("#jobs-refresh").addEventListener("click", refreshJobs);

// -- init ---------------------------------------------------------------------

const savedTab = localStorage.getItem(TAB_STORAGE_KEY);
switchTab(savedTab && VALID_TABS.has(savedTab) ? savedTab : "download", { save: false });
loadLibrary();
loadExports();
