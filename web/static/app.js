const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const state = {
  downloadSymbols: new Set(),
  downloadAvailability: new Map(),
  searchCache: new Map(),
  gapSymbols: new Set(),
  autoSymbols: new Set(),
  automations: [],
  jobStatuses: new Map(),
  jobsPollTimer: null,
  datePickers: {},
};

// -- utils --------------------------------------------------------------------

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

function readDateInput(pickerId, label) {
  const picker = state.datePickers[pickerId];
  const raw = picker?.getValue();
  if (!raw) {
    throw new Error(`${label} is required`);
  }
  return raw;
}

function initDatePickers() {
  const max = formatDateValue(new Date());
  const onDownloadDateChange = () => refreshDownloadRangeNotice();
  const pairs = [
    ["dl-start-picker", dates.start],
    ["dl-end-picker", dates.end],
    ["ex-start-picker", dates.start],
    ["ex-end-picker", dates.end],
    ["gp-start-picker", dates.start],
    ["gp-end-picker", dates.end],
  ];
  pairs.forEach(([id, value]) => {
    const el = document.getElementById(id);
    if (!el) return;
    const onChange = id.startsWith("dl-") ? onDownloadDateChange : null;
    state.datePickers[id] = new DatePicker(el, {
      value,
      min: "1970-01-01",
      max,
      onChange,
    });
  });
}

function setPickerBounds(startId, endId, earliest, latest) {
  const start = state.datePickers[startId];
  const end = state.datePickers[endId];
  if (!start || !end) return;
  if (earliest) {
    start.setMinMax(earliest, latest || start.max);
    if (!start.getValue() || start.getValue() < earliest) start.setValue(earliest);
  }
  if (latest) {
    end.setMinMax(earliest || end.min, latest);
    if (!end.getValue() || end.getValue() > latest) end.setValue(latest);
  }
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
const VALID_TABS = new Set(["download", "export", "gaps", "library", "jobs", "settings"]);

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
  if (tabName === "settings") loadSettings();
  if (save) localStorage.setItem(TAB_STORAGE_KEY, tabName);
}

$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => switchTab(tab.dataset.tab));
});

// -- instrument search (keyboard navigable combobox) ----------------------------

function formatEarliestBadge(r) {
  if (r.earliest_date) return `<span class="suggestion-meta">from ${r.earliest_date}</span>`;
  return "";
}

function formatStoredBadge(r) {
  if (r.stored_from_date) return `<span class="suggestion-meta">from ${r.stored_from_date}</span>`;
  return "";
}

function earliestFromInfo(info) {
  if (!info) return null;
  return info.effective_earliest_date || info.earliest_date || info.jetta_earliest_date || null;
}

function renderExportSymbol(info) {
  const panel = $("#ex-availability");
  if (!info) {
    panel.classList.add("hidden");
    panel.innerHTML = "";
    return;
  }
  const from = earliestFromInfo(info);
  const fromBadge = from
    ? `<span class="suggestion-meta">from ${from}</span>`
    : "";
  panel.classList.remove("hidden");
  panel.innerHTML = `<span class="chip">${info.symbol}${fromBadge}</span>`;
}

function downloadChipHtml(symbol, info) {
  const from = earliestFromInfo(info);
  const fromBadge = from ? `<span class="suggestion-meta">from ${from}</span>` : "";
  return `<span class="chip" data-symbol="${symbol}">
    <strong>${symbol}</strong>${fromBadge}
    <button type="button" data-remove="${symbol}" aria-label="Remove ${symbol}">×</button>
  </span>`;
}

function renderDownloadSymbolList() {
  const panel = $("#dl-symbols");
  const syms = [...state.downloadSymbols];
  panel.innerHTML = syms
    .map((s) => downloadChipHtml(s, state.downloadAvailability.get(s)))
    .join("");
  panel.querySelectorAll("[data-remove]").forEach((btn) => {
    btn.addEventListener("click", () => removeDownloadSymbol(btn.dataset.remove));
  });
  updateDownloadDateBounds();
  refreshDownloadRangeNotice();
}

function getClampedSymbols(start, symbols = [...state.downloadSymbols]) {
  return symbols
    .map((sym) => {
      const earliest = earliestFromInfo(state.downloadAvailability.get(sym));
      if (!earliest || start >= earliest) return null;
      return { sym, earliest };
    })
    .filter(Boolean);
}

function refreshDownloadRangeNotice() {
  const notice = $("#dl-range-notice");
  if (!notice) return;
  const syms = [...state.downloadSymbols];
  if (syms.length < 1) {
    notice.classList.add("hidden");
    return;
  }
  let start;
  try {
    start = state.datePickers["dl-start-picker"]?.getValue();
    if (!start) throw new Error();
  } catch {
    notice.classList.add("hidden");
    return;
  }
  const clamped = getClampedSymbols(start, syms);
  if (!clamped.length) {
    notice.classList.add("hidden");
    return;
  }
  notice.classList.remove("hidden");
  const list = clamped
    .map(({ sym, earliest }) => `<strong>${sym}</strong> from ${earliest}`)
    .join(" · ");
  notice.innerHTML =
    syms.length > 1
      ? `Your start date is before data exists for: ${list}. Each symbol downloads from its own earliest date — nothing fails, ranges just differ.`
      : `Data for <strong>${clamped[0].sym}</strong> starts ${clamped[0].earliest}; download will begin there, not ${start}.`;
}

function updateDownloadDateBounds() {
  const syms = [...state.downloadSymbols];
  if (syms.length !== 1) return;
  const info = state.downloadAvailability.get(syms[0]);
  const earliest = earliestFromInfo(info);
  if (!earliest) return;
  const latest = info?.latest_downloadable_date || formatDateValue(new Date());
  setPickerBounds("dl-start-picker", "dl-end-picker", earliest, latest);
}

function addDownloadSymbol(symbol, searchResult = null) {
  if (state.downloadSymbols.has(symbol)) return;
  state.downloadSymbols.add(symbol);
  const info = searchResult || state.searchCache.get(symbol) || { symbol };
  state.downloadAvailability.set(symbol, info);
  renderDownloadSymbolList();
}

function removeDownloadSymbol(symbol) {
  state.downloadSymbols.delete(symbol);
  state.downloadAvailability.delete(symbol);
  renderDownloadSymbolList();
}

async function fetchAvailability(symbol) {
  try {
    return await api(`/api/instruments/${encodeURIComponent(symbol)}/availability`);
  } catch {
    return null;
  }
}

function setupSearch(inputId, suggestionsId, onPick, options = {}) {
  const { single = false, onResultsChange = null, formatBadge = formatEarliestBadge } = options;
  const input = $(inputId);
  const box = $(suggestionsId);
  let timer = null;
  let results = [];
  let highlight = -1;

  function close() {
    box.classList.remove("open");
    input.setAttribute("aria-expanded", "false");
    highlight = -1;
  }

  function open() {
    box.classList.add("open");
    input.setAttribute("aria-expanded", "true");
  }

  function renderSuggestions() {
    if (!results.length) {
      box.innerHTML = `<div class="suggestion suggestion-empty"><span>No matches</span></div>`;
      open();
      return;
    }
    box.innerHTML = results
      .map((r, i) => {
        const active = i === highlight ? " active" : "";
        return `<div class="suggestion${active}" role="option" data-index="${i}" data-symbol="${r.symbol}" aria-selected="${i === highlight}">
          <div class="suggestion-main"><strong>${r.symbol}</strong> <span class="suggestion-name">${r.name}</span></div>
          <div class="suggestion-sub">${r.description || ""} ${formatBadge(r)}</div>
        </div>`;
      })
      .join("");
    open();
    box.querySelectorAll(".suggestion[data-symbol]").forEach((el) => {
      el.addEventListener("mousedown", (e) => {
        e.preventDefault();
        pick(Number(el.dataset.index));
      });
    });
    const activeEl = box.querySelector(".suggestion.active");
    if (activeEl) activeEl.scrollIntoView({ block: "nearest" });
  }

  function pick(index) {
    const r = results[index];
    if (!r) return;
    onPick(r.symbol, r);
    input.value = single ? r.symbol : "";
    close();
    input.focus();
  }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 1) {
      close();
      box.innerHTML = "";
      return;
    }
    timer = setTimeout(async () => {
      try {
        const { results: found } = await api(
          `/api/instruments/search?q=${encodeURIComponent(q)}&limit=12`,
        );
        results = found;
        found.forEach((r) => state.searchCache.set(r.symbol, r));
        highlight = found.length ? 0 : -1;
        renderSuggestions();
        if (onResultsChange) onResultsChange(found);
      } catch (e) {
        toastError(e.message);
      }
    }, 180);
  });

  input.addEventListener("keydown", (e) => {
    if (!box.classList.contains("open") && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      if (results.length) renderSuggestions();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!results.length) return;
      highlight = Math.min(highlight + 1, results.length - 1);
      renderSuggestions();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!results.length) return;
      highlight = Math.max(highlight - 1, 0);
      renderSuggestions();
    } else if (e.key === "Enter") {
      if (box.classList.contains("open") && highlight >= 0) {
        e.preventDefault();
        pick(highlight);
      }
    } else if (e.key === "Escape") {
      close();
    }
  });

  document.addEventListener("click", (e) => {
    if (!input.contains(e.target) && !box.contains(e.target)) close();
  });
}

async function refreshExportAvailability(symbol) {
  if (!symbol) {
    $("#ex-availability").classList.add("hidden");
    return;
  }
  const info = await fetchAvailability(symbol);
  renderExportSymbol(info);
  if (info) {
    setPickerBounds(
      "ex-start-picker",
      "ex-end-picker",
      info.stored?.first_hour?.slice(0, 10) || info.effective_earliest_date,
      info.stored?.last_hour?.slice(0, 10) || info.latest_downloadable_date,
    );
  }
}

setupSearch("#dl-search", "#dl-suggestions", (symbol, result) => {
  addDownloadSymbol(symbol, result);
});

setupSearch(
  "#ex-search",
  "#ex-suggestions",
  (symbol) => {
    $("#ex-symbol").value = symbol;
    $("#ex-search").value = symbol;
    refreshExportAvailability(symbol);
  },
  { single: true },
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

setupSearch(
  "#gp-search",
  "#gp-suggestions",
  (symbol) => {
    state.gapSymbols.add(symbol);
    renderGapChips();
  },
  { formatBadge: formatStoredBadge },
);

// -- dates defaults -----------------------------------------------------------

const dates = defaultDates();
initDatePickers();
refreshDownloadRangeNotice();

function toggleDateFields(checkboxId, datesId) {
  $(checkboxId).addEventListener("change", (e) => {
    $(datesId).style.opacity = e.target.checked ? "0.4" : "1";
    $(datesId).style.pointerEvents = e.target.checked ? "none" : "auto";
  });
}

toggleDateFields("#gp-all", "#gp-dates");
toggleDateFields("#ex-all", "#ex-dates");

// -- job updates ----------------------------------------------------------------

function renderProfileBlock(p) {
  if (!p.profile || !p.profile_recent?.length) return "";
  const rows = [...p.profile_recent].reverse().slice(0, 50);
  const summary = p.profile_summary;
  let html = '<div class="profile-panel">';
  if (summary) {
    html += `<div class="profile-summary">avg fetch <strong>${summary.fetch_ms}</strong>ms · decode <strong>${summary.decode_ms}</strong>ms · write <strong>${summary.write_ms}</strong>ms · total <strong>${summary.total_ms}</strong>ms <span class="muted">(${summary.samples} samples)</span></div>`;
  }
  html += `<div class="profile-table-wrap"><table class="profile-table"><thead><tr>
    <th>Hour (UTC)</th><th>Sym</th><th>St</th><th>Ticks</th><th>Fetch</th><th>Decode</th><th>Write</th><th>Total</th>
  </tr></thead><tbody>`;
  for (const row of rows) {
    const st = row.skipped ? "skip" : row.status === "completed" ? "ok" : row.status;
    const ticks = row.skipped ? "—" : fmt(row.ticks);
    const fetch = row.skipped ? "—" : `${row.fetch_ms}`;
    const decode = row.skipped ? "—" : `${row.decode_ms}`;
    const write = row.skipped ? "—" : `${row.write_ms}`;
    html += `<tr class="profile-row-${st}"><td>${row.hour}</td><td>${row.symbol}</td><td>${st}</td><td>${ticks}</td><td>${fetch}</td><td>${decode}</td><td>${write}</td><td>${row.total_ms}</td></tr>`;
  }
  html += "</tbody></table></div></div>";
  return html;
}

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
    if (p.workers) {
      html += `
    <div class="progress-meta throttle-meta">
      <span>Workers: <strong>${p.workers}</strong></span>
    </div>`;
    }
    html += renderProfileBlock(p);
  }

  if (p.plans && Object.keys(p.plans).length) {
    const clamped = Object.entries(p.plans).filter(([, plan]) => plan.clamped_start);
    if (clamped.length) {
      html += `<div class="range-notice plan-notice">${clamped
        .map(
          ([sym, plan]) =>
            `<span><strong>${sym}</strong> from ${plan.clamped_start.slice(0, 10)}</span>`,
        )
        .join(" · ")}</div>`;
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
    toastSuccess("Job cancelled");
    refreshJobs();
  } catch (e) {
    toastError(e.message);
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
      if (job.status === "completed") toastSuccess("Job completed");
      else if (job.status === "failed") toastError("Job failed");
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
    start = readDateInput("dl-start-picker", "Start date");
    end = readDateInput("dl-end-picker", "End date");
  } catch (e) {
    return toastError(e.message);
  }
  const clamped = getClampedSymbols(start, symbols);
  if (clamped.length) {
    await showClampAlert(clamped, start);
  }
  const body = {
    symbols,
    start,
    end,
    workers: Number($("#dl-workers").value) || 15,
    force: $("#dl-force").checked,
    profile: $("#dl-profile").checked,
  };
  try {
    const job = await api("/api/download", { method: "POST", body: JSON.stringify(body) });
    toastSuccess(`Download started (${symbols.length} symbol${symbols.length > 1 ? "s" : ""})`);
    prependJob(job);
  } catch (e) {
    toastError(e.message);
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
      body.start = readDateInput("ex-start-picker", "Start date");
      body.end = readDateInput("ex-end-picker", "End date");
    } catch (e) {
      return toastError(e.message);
    }
  }
  try {
    const job = await api("/api/export", { method: "POST", body: JSON.stringify(body) });
    toastSuccess(exportAll ? "Export all started" : "Export started");
    prependJob(job);
  } catch (e) {
    toastError(e.message);
  }
});

async function deleteExport(path, filename) {
  if (
    !(await appConfirm({
      title: "Delete export?",
      text: `Delete ${filename}?`,
      confirmText: "Delete",
    }))
  ) {
    return;
  }
  try {
    await api(`/api/exports/file?path=${encodeURIComponent(path)}`, { method: "DELETE" });
    toastSuccess("Export deleted");
    loadExports();
  } catch (e) {
    toastError(e.message);
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
    body.start = readDateInput("gp-start-picker", "Start date");
    body.end = readDateInput("gp-end-picker", "End date");
  }
  if (repair) body.workers = Number($("#dl-workers").value) || 15;
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
    return toastError(e.message);
  }
  try {
    const r = await api("/api/gaps/preview", { method: "POST", body: JSON.stringify(body) });
    $("#gp-result").innerHTML = renderGapPreviewResult(r);
  } catch (e) {
    toastError(e.message);
  }
}

$("#gp-scan-btn").addEventListener("click", runGapsPreview);

$("#gp-add-library-btn").addEventListener("click", async () => {
  try {
    const { instruments } = await api("/api/status");
    if (!instruments.length) return toast("Library is empty");
    instruments.forEach((i) => state.gapSymbols.add(i.symbol));
    renderGapChips();
    toastSuccess(`Added ${instruments.length} symbol${instruments.length > 1 ? "s" : ""} from library`);
  } catch (e) {
    toastError(e.message);
  }
});

$("#gp-repair-btn").addEventListener("click", async () => {
  let body;
  try {
    body = gapScanBody(true);
  } catch (e) {
    return toastError(e.message);
  }
  try {
    const job = await api("/api/gaps", { method: "POST", body: JSON.stringify(body) });
    toastSuccess("Repair job started");
    prependJob(job);
  } catch (e) {
    toastError(e.message);
  }
});

// -- library ------------------------------------------------------------------

async function deleteLibrarySymbol(symbol) {
  if (
    !(await appConfirm({
      title: `Delete ${symbol}?`,
      text: "All stored data will be removed. This cannot be undone.",
      confirmText: "Delete",
    }))
  ) {
    return;
  }
  try {
    await api(`/api/status/${encodeURIComponent(symbol)}`, { method: "DELETE" });
    state.downloadSymbols.delete(symbol);
    state.downloadAvailability.delete(symbol);
    state.gapSymbols.delete(symbol);
    renderDownloadSymbolList();
    renderGapChips();
    toastSuccess(`${symbol} removed`);
    loadLibrary();
    loadExports();
  } catch (e) {
    toastError(e.message);
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

// -- settings & automations ---------------------------------------------------

function applyTheme(theme) {
  const dark = theme === "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "";
  localStorage.setItem("theme", dark ? "dark" : "light");
  const toggle = $("#set-dark-mode");
  if (toggle) toggle.checked = dark;
}

function previewAutomationDates(daysStart, daysEnd) {
  const today = new Date();
  const offset = (n) => {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    d.setDate(d.getDate() - n);
    return formatDateValue(d);
  };
  const older = Math.max(Number(daysStart) || 0, Number(daysEnd) || 0);
  const newer = Math.min(Number(daysStart) || 0, Number(daysEnd) || 0);
  const start = offset(older);
  const end = offset(newer);
  return start === end ? start : `${end} → ${start}`;
}

function updateAutoDatePreview() {
  const el = $("#auto-date-preview");
  if (!el) return;
  const start = $("#auto-days-start").value;
  const end = $("#auto-days-end").value;
  el.textContent = `Next run will download: ${previewAutomationDates(start, end)} (local date)`;
}

function toggleAutoCustomSymbols() {
  const custom = document.querySelector('input[name="auto-symbols-source"]:checked')?.value === "custom";
  $("#auto-custom-symbols").classList.toggle("hidden", !custom);
}

function renderAutoChips() {
  const wrap = $("#auto-chips");
  if (!wrap) return;
  wrap.innerHTML = [...state.autoSymbols]
    .map(
      (sym) =>
        `<span class="chip">${sym}<button type="button" data-auto-remove="${sym}" aria-label="Remove">×</button></span>`,
    )
    .join("");
  wrap.querySelectorAll("[data-auto-remove]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.autoSymbols.delete(btn.dataset.autoRemove);
      renderAutoChips();
    });
  });
}

function renderAutomationsList() {
  const el = $("#auto-list");
  if (!state.automations.length) {
    el.innerHTML = '<p class="empty">No automatic actions yet. Create one to keep your library fresh.</p>';
    return;
  }
  el.innerHTML = state.automations
    .map((rule) => {
      const action = rule.action || {};
      const symLabel =
        action.symbols_source === "library"
          ? "All library symbols"
          : `${(action.symbols || []).length} symbol(s)`;
      const last = rule.last_run_at
        ? `Last run ${rule.last_run_at.replace("T", " ").replace("Z", " UTC")}`
        : "Never run";
      return `<div class="auto-card" data-auto-id="${rule.id}">
        <div class="auto-card-head">
          <h3>${rule.name}<span class="auto-badge ${rule.enabled ? "on" : "off"}">${rule.enabled ? "On" : "Off"}</span></h3>
          <div class="auto-card-actions">
            <button type="button" class="btn btn-sm" data-auto-run="${rule.id}">Run now</button>
            <button type="button" class="btn btn-sm" data-auto-edit="${rule.id}">Edit</button>
            <button type="button" class="btn btn-sm" data-auto-delete="${rule.id}">Delete</button>
          </div>
        </div>
        <div class="auto-card-meta">
          Daily at <strong>${rule.schedule?.time || "00:00"}</strong> · ${symLabel}<br>
          Dates: <strong>${rule.date_preview || "—"}</strong> · ${last}
        </div>
      </div>`;
    })
    .join("");

  el.querySelectorAll("[data-auto-run]").forEach((btn) => {
    btn.addEventListener("click", () => runAutomationNow(btn.dataset.autoRun));
  });
  el.querySelectorAll("[data-auto-edit]").forEach((btn) => {
    btn.addEventListener("click", () => openAutoEditor(btn.dataset.autoEdit));
  });
  el.querySelectorAll("[data-auto-delete]").forEach((btn) => {
    btn.addEventListener("click", () => deleteAutomation(btn.dataset.autoDelete));
  });
}

function openAutoEditor(ruleId = null) {
  const editor = $("#auto-editor");
  editor.classList.remove("hidden");
  $("#auto-edit-id").value = ruleId || "";
  $("#auto-editor-title").textContent = ruleId ? "Edit action" : "New action";

  if (ruleId) {
    const rule = state.automations.find((r) => r.id === ruleId);
    if (!rule) return;
    const action = rule.action || {};
    $("#auto-name").value = rule.name;
    $("#auto-enabled").checked = rule.enabled;
    $("#auto-time").value = rule.schedule?.time || "00:00";
    $("#auto-workers").value = action.workers ?? 15;
    document.querySelector(`input[name="auto-symbols-source"][value="${action.symbols_source || "library"}"]`).checked = true;
    state.autoSymbols = new Set(action.symbols || []);
    renderAutoChips();
    $("#auto-days-start").value = action.days_ago_start ?? 2;
    $("#auto-days-end").value = action.days_ago_end ?? 2;
    $("#auto-force").checked = !!action.force;
    $("#auto-profile").checked = !!action.profile;
  } else {
    $("#auto-name").value = "";
    $("#auto-enabled").checked = true;
    $("#auto-time").value = "00:00";
    $("#auto-workers").value = $("#set-default-workers")?.value || 15;
    document.querySelector('input[name="auto-symbols-source"][value="library"]').checked = true;
    state.autoSymbols = new Set();
    renderAutoChips();
    $("#auto-days-start").value = 2;
    $("#auto-days-end").value = 2;
    $("#auto-force").checked = false;
    $("#auto-profile").checked = false;
  }
  toggleAutoCustomSymbols();
  updateAutoDatePreview();
  editor.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function closeAutoEditor() {
  $("#auto-editor").classList.add("hidden");
  $("#auto-edit-id").value = "";
}

async function saveAutomation() {
  const name = $("#auto-name").value.trim();
  if (!name) return toast("Name is required");
  const symbolsSource = document.querySelector('input[name="auto-symbols-source"]:checked')?.value || "library";
  if (symbolsSource === "custom" && !state.autoSymbols.size) {
    return toast("Add at least one symbol or choose library");
  }
  const body = {
    name,
    enabled: $("#auto-enabled").checked,
    schedule: { type: "daily", time: $("#auto-time").value || "00:00" },
    action: {
      type: "download",
      symbols_source: symbolsSource,
      symbols: [...state.autoSymbols],
      days_ago_start: Number($("#auto-days-start").value),
      days_ago_end: Number($("#auto-days-end").value),
      workers: Number($("#auto-workers").value) || 15,
      force: $("#auto-force").checked,
      profile: $("#auto-profile").checked,
    },
  };
  try {
    const ruleId = $("#auto-edit-id").value;
    if (ruleId) {
      await api(`/api/automations/${ruleId}`, { method: "PUT", body: JSON.stringify(body) });
      toastSuccess("Action updated");
    } else {
      await api("/api/automations", { method: "POST", body: JSON.stringify(body) });
      toastSuccess("Action created");
    }
    closeAutoEditor();
    await loadSettings();
  } catch (e) {
    toastError(e.message);
  }
}

async function deleteAutomation(ruleId) {
  const rule = state.automations.find((r) => r.id === ruleId);
  if (
    !(await appConfirm({
      title: "Delete action?",
      text: rule ? `Remove "${rule.name}"?` : "Remove this action?",
      confirmText: "Delete",
    }))
  ) {
    return;
  }
  try {
    await api(`/api/automations/${ruleId}`, { method: "DELETE" });
    toastSuccess("Action deleted");
    if ($("#auto-edit-id").value === ruleId) closeAutoEditor();
    await loadSettings();
  } catch (e) {
    toastError(e.message);
  }
}

async function runAutomationNow(ruleId) {
  try {
    const result = await api(`/api/automations/${ruleId}/run`, { method: "POST" });
    if (result.skipped) {
      toast(result.reason || "Skipped");
    } else {
      toastSuccess(`Download started (${result.symbol_count} symbols)`);
      if (result.job_id) {
        const job = await api(`/api/jobs/${result.job_id}`);
        prependJob(job);
      }
    }
    await loadSettings();
  } catch (e) {
    toastError(e.message);
  }
}

async function loadSettings() {
  try {
    const data = await api("/api/settings");
    applyTheme(data.ui?.theme || "light");
    const workers = data.ui?.default_workers ?? 15;
    $("#set-default-workers").value = workers;
    const dlWorkers = $("#dl-workers");
    if (dlWorkers && !dlWorkers.dataset.userTouched) dlWorkers.value = workers;
    state.automations = data.automations || [];
    renderAutomationsList();
  } catch (e) {
    $("#auto-list").innerHTML = `<p class="empty">${e.message}</p>`;
  }
}

async function saveUiSettings(patch) {
  try {
    const { ui } = await api("/api/settings/ui", { method: "PATCH", body: JSON.stringify(patch) });
    if (ui.theme) applyTheme(ui.theme);
    if (ui.default_workers != null) {
      $("#set-default-workers").value = ui.default_workers;
      const dlWorkers = $("#dl-workers");
      if (dlWorkers && !dlWorkers.dataset.userTouched) dlWorkers.value = ui.default_workers;
    }
  } catch (e) {
    toastError(e.message);
  }
}

$("#set-dark-mode")?.addEventListener("change", (e) => {
  const theme = e.target.checked ? "dark" : "light";
  applyTheme(theme);
  saveUiSettings({ theme });
});

$("#set-default-workers")?.addEventListener("change", (e) => {
  saveUiSettings({ default_workers: Number(e.target.value) || 15 });
});

$("#dl-workers")?.addEventListener("input", () => {
  $("#dl-workers").dataset.userTouched = "1";
});

$("#auto-add-btn")?.addEventListener("click", () => openAutoEditor());
$("#auto-cancel-btn")?.addEventListener("click", closeAutoEditor);
$("#auto-save-btn")?.addEventListener("click", saveAutomation);
$$('input[name="auto-symbols-source"]').forEach((el) => {
  el.addEventListener("change", toggleAutoCustomSymbols);
});
["auto-days-start", "auto-days-end"].forEach((id) => {
  $(`#${id}`)?.addEventListener("input", updateAutoDatePreview);
});

setupSearch(
  "#auto-search",
  "#auto-suggestions",
  (symbol) => {
    state.autoSymbols.add(symbol);
    renderAutoChips();
  },
  { formatBadge: () => "" },
);

// -- init ---------------------------------------------------------------------

const savedTab = localStorage.getItem(TAB_STORAGE_KEY);
switchTab(savedTab && VALID_TABS.has(savedTab) ? savedTab : "download", { save: false });
loadSettings();
loadLibrary();
loadExports();
