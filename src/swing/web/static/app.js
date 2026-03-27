/* ──────────────────────────────────────────────────────────────
   ScreenerX.ai — dashboard
   ────────────────────────────────────────────────────────────── */

let allCandidates = [];
let currentCurrency = "₹";
let currentSort = { key: "score", ascending: false };

// ── Helpers ──
function formatTime(utcIso) {
    const d = new Date(utcIso);
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", hour12: true });
}

// ── Fetch with session cookie; redirect if Google auth required ──
function authFetch(url, options = {}) {
    return fetch(url, { credentials: "include", ...options }).then((response) => {
        if (response.status === 401) {
            window.location.href = "/login";
            return Promise.reject(new Error("Unauthorized"));
        }
        return response;
    });
}

// ── Auto-load cached results on page load ──
document.addEventListener("DOMContentLoaded", () => {
    loadCachedResults();
    initAuthBar();
});

async function initAuthBar() {
    const strip = document.getElementById("authStrip");
    if (!strip) return;
    try {
        const r = await fetch("/api/me", { credentials: "include" });
        const d = await r.json();
        if (!d.auth_configured) return;
        if (!d.authenticated) {
            window.location.href = "/login";
            return;
        }
        strip.hidden = false;
        if (d.picture) {
            const img = document.getElementById("authPic");
            img.src = d.picture;
            img.hidden = false;
        }
        const label = document.getElementById("authEmail");
        if (label) label.textContent = d.name || d.email || "";
        if (d.is_admin) document.getElementById("adminLink").hidden = false;
    } catch {
        /* ignore */
    }
}

async function loadCachedResults() {
    const scopeSelect = document.getElementById("scanScope");
    const market = scopeSelect.value;

    try {
        const response = await authFetch(`/api/results?market=${market}`);
        if (!response.ok) return;
        const data = await response.json();

        if (data.cached && data.candidates) {
            displayData(data);
            const statusText = document.getElementById("statusText");
            statusText.textContent = `${data.count} candidates · scanned at ${formatTime(data.scanned_at)}`;
        }
    } catch {
        // Silently fail — user can click Scan Now manually
    }
}

// ── Scan ──
async function startScan(ev) {
    const forceFresh = ev && ev.shiftKey === true;
    const btn = document.getElementById("btnScan");
    const loading = document.getElementById("loadingSection");
    const empty = document.getElementById("emptyState");
    const results = document.getElementById("resultsSection");
    const statsBar = document.getElementById("statsBar");
    const pulse = document.querySelector(".pulse");
    const statusText = document.getElementById("statusText");
    const scopeSelect = document.getElementById("scanScope");
    const market = scopeSelect.value;

    btn.disabled = true;
    scopeSelect.disabled = true;
    btn.innerHTML = '<span class="btn-icon" aria-hidden="true"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg></span><span>Scanning…</span>';
    loading.style.display = "flex";
    empty.style.display = "none";
    results.style.display = "none";
    statsBar.style.display = "none";
    pulse.classList.add("scanning");
    statusText.textContent = `Scanning ${market.replace("_", " ")} stocks...`;

    try {
        const scanUrl = `/api/scan?market=${market}${forceFresh ? "&fresh=true" : ""}`;
        const response = await authFetch(scanUrl);
        if (!response.ok) {
            let msg = `HTTP ${response.status}`;
            try {
                const errBody = await response.json();
                if (errBody.error) msg = errBody.error;
            } catch {
                /* ignore */
            }
            throw new Error(msg);
        }

        const data = await response.json();
        displayData(data);

        if (data.cached) {
            statusText.textContent = `${data.count} candidates · scanned at ${formatTime(data.scanned_at)}`;
        } else {
            statusText.textContent = `${data.count} candidates found · scanned at ${formatTime(data.scanned_at)}`;
        }
        pulse.classList.remove("scanning");
    } catch (err) {
        console.error("Scan failed:", err);
        statusText.textContent = "Scan failed";
        pulse.classList.remove("scanning");
        empty.style.display = "flex";
        empty.querySelector("h2").textContent = "Scan Failed";
        const errP = empty.querySelector(".empty-lead") || empty.querySelector("p");
        if (errP) {
            errP.innerHTML =
                `<span style="color:var(--danger, #fb7185)">Error: ${err.message}</span><br>Check that the server is running.`;
        }
    } finally {
        loading.style.display = "none";
        btn.disabled = false;
        scopeSelect.disabled = false;
        btn.innerHTML = '<span class="btn-icon" aria-hidden="true"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.35-4.35"/></svg></span><span>Run scan</span>';
    }
}

// ── Refresh current prices (yfinance live quote; updates CMP column) ──
async function refreshCurrentPrices() {
    const tickers = allCandidates.map((c) => c.ticker || c.symbol).filter(Boolean);
    if (tickers.length === 0) return;
    try {
        const response = await authFetch("/api/quotes?tickers=" + encodeURIComponent(tickers.join(",")));
        if (!response.ok) return;
        const prices = await response.json();
        document.querySelectorAll("tr[data-ticker]").forEach((row) => {
            const ticker = row.dataset.ticker;
            const price = prices[ticker];
            const cell = row.querySelector(".cell-price");
            if (cell && typeof price === "number" && !Number.isNaN(price)) {
                cell.textContent = currentCurrency + price.toFixed(2);
            }
        });
    } catch {
        // ignore
    }
}

// ── Display Data (shared by auto-load and scan) ──
function displayData(data) {
    const statsBar = document.getElementById("statsBar");
    const results = document.getElementById("resultsSection");
    const empty = document.getElementById("emptyState");

    allCandidates = data.candidates || [];
    currentCurrency = data.currency || "₹";

    // Update table headers with currency
    document.querySelector(".col-price").textContent = `CMP (${currentCurrency}) ↕`;
    document.querySelector(".col-entry").textContent = `Entry (${currentCurrency})`;
    document.querySelector(".col-sl").textContent = `Stop Loss (${currentCurrency})`;
    document.querySelector(".col-target").textContent = `Target (${currentCurrency})`;

    // Update stats
    document.getElementById("statCandidates").textContent = data.count || 0;
    document.getElementById("statScanned").textContent = data.stats?.scanned || 0;
    document.getElementById("statFiltered").textContent = data.stats?.filtered || 0;

    if (allCandidates.length > 0) {
        const avgScore =
            allCandidates.reduce((s, c) => s + c.score, 0) / allCandidates.length;
        document.getElementById("statAvgScore").textContent = avgScore.toFixed(1);
    } else {
        document.getElementById("statAvgScore").textContent = "—";
    }

    // Populate industry filter
    populateIndustryFilter();

    // Render
    renderResults(allCandidates);
    // Fetch current prices and update CMP column (runs in background)
    refreshCurrentPrices();

    statsBar.style.display = "grid";
    empty.style.display = "none";
    results.style.display = "block";
}

// ── Render Results ──
function renderResults(candidates) {
    const tbody = document.getElementById("resultsBody");
    tbody.innerHTML = "";

    if (candidates.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="13" style="text-align:center;padding:40px;color:var(--text-dim)">
                    No candidates match your filters.
                </td>
            </tr>`;
        return;
    }

    candidates.forEach((c, idx) => {
        const tr = document.createElement("tr");
        tr.classList.add("fade-in");
        tr.style.animationDelay = `${idx * 30}ms`;
        tr.dataset.symbol = c.symbol;
        tr.dataset.ticker = c.ticker || c.symbol;

        const scoreClass =
            c.score >= 70 ? "score-high" : c.score >= 50 ? "score-mid" : "score-low";

        const rsiClass =
            c.latest.rsi <= 50
                ? "rsi-cool"
                : c.latest.rsi <= 65
                    ? "rsi-warm"
                    : "rsi-hot";

        const signalNames = {
            ema_aligned: { icon: "📈", label: "EMA" },
            rsi_recovery: { icon: "📊", label: "RSI" },
            macd_crossover: { icon: "🔀", label: "MACD" },
            support_bounce: { icon: "🔄", label: "SUP" },
            volume_surge: { icon: "📢", label: "VOL" },
        };

        let signalHTML = '<div class="signal-pills">';
        for (const [key, meta] of Object.entries(signalNames)) {
            const active = c.signals[key];
            signalHTML += `<span class="signal-pill ${active ? "" : "inactive"}">${meta.icon} ${meta.label}</span>`;
        }
        signalHTML += "</div>";

        // Canvas ID for sparkline
        const canvasId = `spark-${c.symbol}`;

        tr.innerHTML = `
            <td class="col-rank" style="text-align:center;color:var(--text-dim)">${idx + 1}</td>
            <td class="col-symbol"><span class="cell-symbol">${c.symbol}</span></td>
            <td class="col-company"><span class="cell-company">${c.company}</span></td>
            <td class="col-industry"><span class="cell-industry">${c.industry}</span></td>
            <td class="col-chart"><canvas id="${canvasId}" class="sparkline-canvas" width="110" height="36"></canvas></td>
            <td class="col-score" style="text-align:center"><span class="score-badge ${scoreClass}" onclick="toggleBreakdown('${c.symbol}')" title="Click to see score breakdown">${c.score}</span></td>
            <td class="col-price" style="text-align:right"><span class="cell-price">${currentCurrency}${c.latest.close.toFixed(2)}</span></td>
            <td class="col-entry" style="text-align:right"><span class="cell-entry">${currentCurrency}${c.levels.entry.toFixed(2)}</span></td>
            <td class="col-sl" style="text-align:right"><span class="cell-sl">${currentCurrency}${c.levels.stop_loss.toFixed(2)}</span></td>
            <td class="col-target" style="text-align:right"><span class="cell-target">${currentCurrency}${c.levels.primary_target.toFixed(2)}</span></td>
            <td class="col-rr" style="text-align:center"><span class="cell-rr">${c.levels.risk_reward.toFixed(1)}</span></td>
            <td class="col-signals">${signalHTML}</td>
            <td class="col-rsi" style="text-align:center"><span class="rsi-value ${rsiClass}">${c.latest.rsi ? c.latest.rsi.toFixed(0) : "—"}</span></td>
        `;

        tbody.appendChild(tr);

        // Draw sparkline after DOM insert
        requestAnimationFrame(() => drawSparkline(canvasId, c.sparkline));
    });
}

// ── Score Breakdown Toggle ──
function toggleBreakdown(symbol) {
    const existingRow = document.getElementById(`breakdown-${symbol}`);
    if (existingRow) {
        existingRow.remove();
        return;
    }

    // Close any other open breakdown
    document.querySelectorAll(".breakdown-row").forEach((r) => r.remove());

    const candidate = allCandidates.find((c) => c.symbol === symbol);
    if (!candidate || !candidate.score_breakdown) return;

    const dataRow = document.querySelector(`tr[data-symbol="${symbol}"]`);
    if (!dataRow) return;

    const breakdownTr = document.createElement("tr");
    breakdownTr.id = `breakdown-${symbol}`;
    breakdownTr.classList.add("breakdown-row");

    let factorsHTML = "";
    for (const f of candidate.score_breakdown) {
        const barClass =
            f.raw_score >= 70 ? "bar-high" : f.raw_score >= 40 ? "bar-mid" : "bar-low";
        const weightPct = (f.weight * 100).toFixed(0);

        factorsHTML += `
            <div class="factor-card">
                <div class="factor-top">
                    <span class="factor-name">${f.name}</span>
                    <div class="factor-values">
                        <span class="factor-raw">${f.raw_score}</span>
                        <span class="factor-weight-label">×${weightPct}%</span>
                        <span class="factor-weighted">= ${f.weighted}</span>
                    </div>
                </div>
                <div class="factor-bar-track">
                    <div class="factor-bar-fill ${barClass}" style="width: ${f.raw_score}%"></div>
                </div>
                <div class="factor-reason">${f.reason}</div>
            </div>
        `;
    }

    breakdownTr.innerHTML = `
        <td colspan="13">
            <div class="breakdown-panel">
                <div class="breakdown-header">
                    🧠 Score Breakdown — ${symbol} — ${candidate.score} / 100
                </div>
                <div class="breakdown-factors">
                    ${factorsHTML}
                </div>
            </div>
        </td>
    `;

    dataRow.after(breakdownTr);
}

// ── Sparkline Drawing ──
function drawSparkline(canvasId, data) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !data || data.length < 2) return;

    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    const padding = 2;

    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;

    const isUp = data[data.length - 1] >= data[0];
    const color = isUp ? "#06d6a0" : "#ef476f";

    ctx.clearRect(0, 0, w, h);

    // Draw line
    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";

    data.forEach((val, i) => {
        const x = padding + (i / (data.length - 1)) * (w - 2 * padding);
        const y = h - padding - ((val - min) / range) * (h - 2 * padding);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // Draw gradient fill
    const lastX = padding + ((data.length - 1) / (data.length - 1)) * (w - 2 * padding);
    const lastY =
        h - padding - ((data[data.length - 1] - min) / range) * (h - 2 * padding);

    ctx.lineTo(lastX, h);
    ctx.lineTo(padding, h);
    ctx.closePath();

    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, isUp ? "rgba(6,214,160,0.15)" : "rgba(239,71,111,0.15)");
    grad.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = grad;
    ctx.fill();
}

// ── Filtering ──
function filterResults() {
    const search = document.getElementById("searchInput").value.toLowerCase();
    const industry = document.getElementById("industryFilter").value;

    const filtered = allCandidates.filter((c) => {
        const matchSearch =
            !search ||
            c.symbol.toLowerCase().includes(search) ||
            c.company.toLowerCase().includes(search);
        const matchIndustry = !industry || c.industry === industry;
        return matchSearch && matchIndustry;
    });

    renderResults(filtered);
}

// ── Sorting ──
function toggleSort(key) {
    if (currentSort.key === key) {
        currentSort.ascending = !currentSort.ascending;
    } else {
        currentSort.key = key;
        currentSort.ascending = false; // default descending for numeric
    }
    sortAndRender();
}

function sortResults() {
    const sel = document.getElementById("sortBy").value;
    currentSort.key = sel;
    currentSort.ascending = false;
    sortAndRender();
}

function sortAndRender() {
    const key = currentSort.key;
    const asc = currentSort.ascending;

    const sorted = [...allCandidates].sort((a, b) => {
        let va, vb;
        if (key === "symbol") {
            va = a.symbol;
            vb = b.symbol;
            return asc ? va.localeCompare(vb) : vb.localeCompare(va);
        }
        if (key === "close") {
            va = a.latest.close;
            vb = b.latest.close;
        } else if (key === "risk_reward") {
            va = a.levels.risk_reward;
            vb = b.levels.risk_reward;
        } else if (key === "signal_count") {
            va = a.signal_count;
            vb = b.signal_count;
        } else {
            va = a[key] ?? 0;
            vb = b[key] ?? 0;
        }
        return asc ? va - vb : vb - va;
    });

    allCandidates = sorted;
    filterResults(); // re-apply search/industry filters
}

// ── Industry Filter Populate ──
function populateIndustryFilter() {
    const sel = document.getElementById("industryFilter");
    const industries = [...new Set(allCandidates.map((c) => c.industry))].sort();
    sel.innerHTML = '<option value="">All Industries</option>';
    industries.forEach((ind) => {
        if (ind) {
            const opt = document.createElement("option");
            opt.value = ind;
            opt.textContent = ind;
            sel.appendChild(opt);
        }
    });
}
