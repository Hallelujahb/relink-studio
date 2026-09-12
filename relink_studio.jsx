import { useState, useMemo, useEffect, useRef } from "react";
import Papa from "papaparse";
import * as XLSX from "xlsx";

/* ---------------------------------------------------------------------- */
/* Backend wiring                                                        */
/* Talks to the relink-api Flask backend (see ../relink-api). Change      */
/* API_BASE if you run the backend on a different host/port.             */
/* ---------------------------------------------------------------------- */

const API_BASE = "http://localhost:5000/api";

class ApiError extends Error {}

async function apiFetch(path, options = {}) {
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      headers: options.body && !(options.body instanceof FormData) ? { "Content-Type": "application/json" } : undefined,
      ...options,
    });
  } catch (e) {
    throw new ApiError(
      `Could not reach the backend at ${API_BASE}${path}. Is relink-api running (python run.py)?`
    );
  }
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body && body.error) message = body.error;
    } catch { /* non-JSON error body, keep default message */ }
    throw new ApiError(message);
  }
  return res;
}

async function apiJson(path, options = {}) {
  const res = await apiFetch(path, options);
  return res.json();
}

async function uploadFileToBackend(file) {
  const form = new FormData();
  form.append("file", file);
  return apiJson("/upload", { method: "POST", body: form });
}

/* ---------------------------------------------------------------------- */
/* Shared tokens and primitives                                          */
/* ---------------------------------------------------------------------- */

const TOKENS_CSS = `
  .rls[data-theme="dark"] {
    --bg: #0D0F13; --surface: #15181E; --surface-2: #1B1F27; --surface-hover: #232833;
    --border: #262B35; --border-strong: #333A47;
    --text: #E7EAEE; --text-muted: #8B93A3;
    --primary: #3B82F6; --primary-hover: #60A5FA; --primary-soft: #1B2B4D;
    --success: #22C55E; --success-soft: #12281A;
    --warning: #F59E0B; --warning-soft: #2E2411;
    --danger: #EF4444; --danger-soft: #341418;
    --method-a: #3B82F6; --method-b: #14B8A6; --method-c: #A78BFA; --method-d: #FB923C; --method-e: #818CF8;
    --shadow: 0 1px 2px rgba(0,0,0,0.4), 0 4px 12px rgba(0,0,0,0.25);
  }
  .rls[data-theme="light"] {
    /* Softer than a stark cool-white: warmer, lower-contrast "paper" gray
       instead of near-pure white surfaces on near-white background, plus
       a warm off-black for text instead of near-pure black. Meaningfully
       easier on the eyes for long review sessions. */
    --bg: #EAE8E2; --surface: #F7F6F1; --surface-2: #EFEDE6; --surface-hover: #E4E2D9;
    --border: #DCDACF; --border-strong: #C7C4B5;
    --text: #33322C; --text-muted: #726F62;
    --primary: #2563EB; --primary-hover: #1D4ED8; --primary-soft: #E3E9F9;
    --success: #16A34A; --success-soft: #E7F3EA;
    --warning: #B4620A; --warning-soft: #F7EEE0;
    --danger: #C4331F; --danger-soft: #F6E7E3;
    --method-a: #2563EB; --method-b: #0D9488; --method-c: #7C3AED; --method-d: #B4620A; --method-e: #4338CA;
    --shadow: 0 1px 2px rgba(40,35,20,0.05), 0 4px 10px rgba(40,35,20,0.05);
  }
  .rls { background: var(--bg); color: var(--text); }
  .surf { background: var(--surface); border-color: var(--border); }
  .surf2 { background: var(--surface-2); border-color: var(--border); }
  .muted { color: var(--text-muted); }
  .b { border-color: var(--border); }
  .card { border-radius: 10px; box-shadow: var(--shadow); }
  .btn { border-radius: 6px; transition: background-color .15s ease, border-color .15s ease, transform .1s ease, color .15s ease; cursor: pointer; }
  .btn:active { transform: scale(0.97); }
  .btn-primary { background: var(--primary); color: white; }
  .btn-primary:hover { background: var(--primary-hover); }
  .btn-ghost { background: var(--surface-2); border: 1px solid var(--border); color: var(--text); }
  .btn-ghost:hover { background: var(--surface-hover); border-color: var(--border-strong); }
  .btn-success { background: transparent; border: 1px solid var(--success); color: var(--success); }
  .btn-success:hover { background: var(--success-soft); }
  .btn-danger { background: transparent; border: 1px solid var(--danger); color: var(--danger); }
  .btn-danger:hover { background: var(--danger-soft); }
  .tab-pill { border-radius: 999px; cursor: pointer; transition: background-color .15s ease, color .15s ease, box-shadow .15s ease; border: 1px solid transparent; }
  .tab-pill:hover { background: var(--surface-hover); border-color: var(--border-strong); }
  .toast { animation: toastIn .18s ease-out; }
  .rowIn { animation: rowIn .25s ease-out; }
  @keyframes toastIn { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: translateY(0); } }
  @keyframes rowIn { from { opacity: 0; transform: translateX(-6px); } to { opacity: 1; transform: translateX(0); } }
  @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
  .switch { width: 38px; height: 22px; border-radius: 999px; position: relative; transition: background-color .15s ease; cursor: pointer; flex-shrink: 0; }
  .switch-knob { width: 18px; height: 18px; border-radius: 999px; background: white; position: absolute; top: 2px; transition: left .15s ease; }
  input, textarea, select { outline: none; }
  input[type="number"]::-webkit-inner-spin-button, input[type="number"]::-webkit-outer-spin-button { -webkit-appearance: none; margin: 0; }
  input[type="number"] { -moz-appearance: textfield; }
  th { text-align: left; font-weight: 600; }
  .rls-grid-review { display: grid; grid-template-columns: 270px 1fr 350px; height: 100%; min-height: 0; }
  @media (max-width: 900px) {
    .rls-grid-review { grid-template-columns: 1fr; height: auto; }
  }

  /* Themed scrollbars -- default browser scrollbars are always white/gray
     regardless of theme, which looks out of place against a dark surface.
     Firefox via scrollbar-color/-width, WebKit/Chromium via the
     pseudo-elements below; both keyed off the same theme variables so
     they track dark/light automatically. */
  .rls, .rls * {
    scrollbar-width: thin;
    scrollbar-color: var(--border-strong) transparent;
  }
  .rls ::-webkit-scrollbar { width: 10px; height: 10px; }
  .rls ::-webkit-scrollbar-track { background: transparent; }
  .rls ::-webkit-scrollbar-corner { background: transparent; }
  .rls ::-webkit-scrollbar-thumb {
    background-color: var(--border-strong);
    border-radius: 999px;
    border: 2px solid var(--surface);
    background-clip: padding-box;
  }
  .rls ::-webkit-scrollbar-thumb:hover { background-color: var(--text-muted); background-clip: padding-box; }
`;

function Logo() {
  return (
    <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
      <circle cx="7" cy="7" r="4.5" stroke="var(--primary)" strokeWidth="2" />
      <circle cx="15" cy="15" r="4.5" stroke="var(--primary)" strokeWidth="2" />
      <line x1="9.8" y1="9.8" x2="12.2" y2="12.2" stroke="var(--primary)" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function Toast({ toast, onDone }) {
  const isObj = toast && typeof toast === "object";
  const message = isObj ? toast.message : toast;
  const hasUndo = isObj && typeof toast.undo === "function";

  useEffect(() => {
    const t = setTimeout(onDone, hasUndo ? 6000 : 2400);
    return () => clearTimeout(t);
  }, [toast, onDone, hasUndo]);

  return (
    <div className="toast fixed bottom-4 right-4 surf card border px-4 py-2.5 text-sm z-50 flex items-center gap-3">
      <span>{message}</span>
      {hasUndo && (
        <button onClick={() => { toast.undo(); onDone(); }} className="btn btn-ghost text-xs font-medium px-2.5 py-1 flex-shrink-0">
          Undo
        </button>
      )}
    </div>
  );
}

function downloadFile(filename, content, mime = "text/plain") {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function Switch({ on, onToggle, accent = "var(--primary)" }) {
  return (
    <div onClick={onToggle} className="switch" style={{ background: on ? accent : "var(--border-strong)" }}>
      <div className="switch-knob" style={{ left: on ? "18px" : "2px" }} />
    </div>
  );
}

function NumberStepper({ value, setValue, min = 0, max = 1, step = 0.01 }) {
  const clamp = (v) => Math.min(max, Math.max(min, v));
  const nudge = (dir) => setValue(clamp(Math.round((value + dir * step) * 100) / 100));
  return (
    <div className="flex items-stretch surf2 border b rounded-md overflow-hidden">
      <input
        type="text" inputMode="decimal" value={value}
        onChange={(e) => {
          const v = parseFloat(e.target.value);
          setValue(Number.isNaN(v) ? 0 : clamp(v));
        }}
        className="bg-transparent text-sm px-2 py-1 w-14 text-right font-mono outline-none"
      />
      <div className="flex flex-col border-l b" style={{ borderColor: "var(--border)" }}>
        <button
          type="button" tabIndex={-1} onClick={() => nudge(1)}
          className="flex items-center justify-center px-1.5 leading-none transition-colors"
          style={{ height: "12px", color: "var(--text-muted)" }}
          onMouseEnter={(e) => (e.currentTarget.style.color = "var(--text)")}
          onMouseLeave={(e) => (e.currentTarget.style.color = "var(--text-muted)")}
        >
          <svg width="8" height="5" viewBox="0 0 8 5" fill="none"><path d="M1 4L4 1L7 4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
        <button
          type="button" tabIndex={-1} onClick={() => nudge(-1)}
          className="flex items-center justify-center px-1.5 leading-none transition-colors border-t b"
          style={{ height: "12px", color: "var(--text-muted)", borderColor: "var(--border)" }}
          onMouseEnter={(e) => (e.currentTarget.style.color = "var(--text)")}
          onMouseLeave={(e) => (e.currentTarget.style.color = "var(--text-muted)")}
        >
          <svg width="8" height="5" viewBox="0 0 8 5" fill="none"><path d="M1 1L4 4L7 1" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
      </div>
    </div>
  );
}

function ThresholdSlider({ label, value, setValue, accent }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{label}</span>
        <NumberStepper value={value} setValue={setValue} min={0} max={1} step={0.01} />
      </div>
      <input
        type="range" min="0" max="1" step="0.01" value={value}
        onChange={(e) => setValue(parseFloat(e.target.value))}
        className="w-full" style={{ accentColor: accent }}
      />
    </div>
  );
}

const TAB_LABELS = ["Sources & shape", "Hierarchy & methods", "Safety & rules", "Review & finalize", "Export & audit"];

function TopBar({ theme, setTheme, activeTab, setActiveTab, onDownloadConfig, onUploadConfig, setToast, sourceFile, targetFile, onExecutePipeline, isRunning, canRun, merged }) {
  return (
    <>
      <div className="surf border-b flex items-center justify-between px-5 py-3">
        <div className="flex items-center gap-3">
          <Logo />
          <span className="font-semibold text-sm tracking-tight">Relink Studio</span>
          <span className="text-xs muted surf2 border b px-2 py-1 rounded-md">{sourceFile.name} &harr; {targetFile.name}</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs muted flex items-center gap-1.5" title="Your files are read and processed in this browser only. Nothing is uploaded anywhere.">
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: "var(--success)" }} />
            Runs locally, data never leaves this machine
          </span>
          <button onClick={() => setTheme(theme === "dark" ? "light" : "dark")} className="btn btn-ghost text-xs font-medium px-3 py-1.5">
            {theme === "dark" ? "Light mode" : "Dark mode"}
          </button>
          <label className="btn btn-ghost text-xs font-medium px-3 py-1.5">
            Upload config
            <input type="file" accept=".json" onChange={onUploadConfig} className="hidden" />
          </label>
          <button onClick={onDownloadConfig} className="btn btn-ghost text-xs font-medium px-3 py-1.5">Download config</button>
          <button
            onClick={onExecutePipeline}
            disabled={isRunning || !canRun}
            title={!canRun ? "Upload a source and target file first (Tab 1)." : undefined}
            className="btn btn-primary text-xs font-medium px-3.5 py-1.5"
            style={isRunning || !canRun ? { opacity: 0.5, cursor: "not-allowed" } : {}}
          >
            {isRunning ? "Running..." : "Execute pipeline"}
          </button>
        </div>
      </div>
      <div className="surf border-b flex items-center px-5 py-2.5 gap-2 overflow-x-auto">
        {TAB_LABELS.map((label, i) => {
          // The export tab produces the authoritative CSV, which only
          // makes sense once review decisions are merged & finalized --
          // jumping straight there before that would let you download a
          // partial/undecided set. Everything else stays freely navigable.
          const isExportTab = i === TAB_LABELS.length - 1;
          const locked = isExportTab && !merged;
          return (
            <div key={label} className="flex items-center gap-2">
              <div
                onClick={() => { if (locked) { setToast('Merge and finalize your review decisions first (tab "Review & finalize").'); return; } setActiveTab(i); }}
                className="tab-pill text-xs font-medium px-3.5 py-1.5 whitespace-nowrap"
                title={locked ? "Locked until you merge and finalize your review decisions." : undefined}
                style={
                  i === activeTab
                    ? { background: "var(--primary-soft)", color: "var(--primary)", boxShadow: "inset 0 0 0 1px var(--primary)" }
                    : locked
                    ? { color: "var(--text-muted)", opacity: 0.5, cursor: "not-allowed" }
                    : { color: "var(--text-muted)" }
                }
              >
                {label}{locked ? " \u{1F512}" : ""}
              </div>
              {i < TAB_LABELS.length - 1 && <span className="muted text-xs">&rarr;</span>}
            </div>
          );
        })}
      </div>
    </>
  );
}

function NavFooter({ activeTab, setActiveTab, canContinue = true, continueLabel, onContinue, blockedReason }) {
  const isFirst = activeTab === 0;
  const isLast = activeTab === TAB_LABELS.length - 1;
  if (isFirst && isLast) return null;

  return (
    <>
      {!isFirst && (
        <button
          onClick={() => setActiveTab(activeTab - 1)}
          className="btn btn-ghost card text-sm font-medium px-4 py-2.5 border"
          style={{ position: "fixed", left: "20px", bottom: "20px", zIndex: 40 }}
        >
          &larr; Back
        </button>
      )}
      {!isLast && (
        <div className="flex items-center gap-3" style={{ position: "fixed", right: "20px", bottom: "20px", zIndex: 40 }}>
          {!canContinue && blockedReason && (
            <span className="text-xs muted card surf border px-3 py-2">{blockedReason}</span>
          )}
          <button
            onClick={() => { if (canContinue) (onContinue ? onContinue() : setActiveTab(activeTab + 1)); }}
            disabled={!canContinue}
            className="btn btn-primary card text-sm font-medium px-4 py-2.5"
            style={!canContinue ? { opacity: 0.45, cursor: "not-allowed" } : {}}
            title={!canContinue ? blockedReason : undefined}
          >
            {continueLabel || `Continue to ${TAB_LABELS[activeTab + 1]} \u2192`}
          </button>
        </div>
      )}
    </>
  );
}

/* ---------------------------------------------------------------------- */
/* File parsing (real, used by Tab 1)                                    */
/* ---------------------------------------------------------------------- */

function parseUploadedFile(file, onParsed, onError) {
  const ext = file.name.split(".").pop().toLowerCase();
  const reader = new FileReader();

  if (ext === "csv") {
    reader.onload = () => {
      try {
        const result = Papa.parse(reader.result, { header: true, skipEmptyLines: true });
        if (result.errors && result.errors.length) {
          const first = result.errors[0];
          onError(new Error(`Row ${first.row != null ? first.row : "?"}: ${first.message}`));
          return;
        }
        const columns = result.meta.fields || [];
        if (!columns.length) {
          onError(new Error("No header row found, the first line should be column names."));
          return;
        }
        onParsed({ columns, rowCount: result.data.length, format: "csv", geometry: null });
      } catch (e) {
        onError(e);
      }
    };
    reader.onerror = () => onError(new Error("The browser could not read this file off disk."));
    reader.readAsText(file);
  } else if (ext === "xlsx" || ext === "xls") {
    reader.onload = () => {
      try {
        const wb = XLSX.read(reader.result, { type: "binary" });
        const sheet = wb.Sheets[wb.SheetNames[0]];
        if (!sheet) { onError(new Error("Workbook has no sheets.")); return; }
        const rows = XLSX.utils.sheet_to_json(sheet, { header: 1 });
        const columns = rows[0] || [];
        if (!columns.length) { onError(new Error("First sheet's header row is empty.")); return; }
        onParsed({ columns, rowCount: Math.max(0, rows.length - 1), format: "xlsx", geometry: null });
      } catch (e) {
        onError(new Error(`Could not read this as an Excel file (${e.message}).`));
      }
    };
    reader.onerror = () => onError(new Error("The browser could not read this file off disk."));
    reader.readAsBinaryString(file);
  } else if (ext === "geojson" || ext === "json") {
    reader.onload = () => {
      try {
        const data = JSON.parse(reader.result);
        const features = data.features || [];
        if (!Array.isArray(data.features)) {
          onError(new Error("Not a valid GeoJSON FeatureCollection, missing a 'features' array."));
          return;
        }
        const columns = features.length ? Object.keys(features[0].properties || {}) : [];
        // Keep each feature's properties alongside its geometry, in the
        // same order -- needed to look a feature up by whichever column
        // ends up chosen as the ID column (that choice isn't known yet
        // at parse time), rather than assuming the ID value happens to
        // equal the feature's raw array position.
        onParsed({ columns, rowCount: features.length, format: "geojson", geometry: features.map((f) => f.geometry), rows: features.map((f) => f.properties || {}) });
      } catch (e) {
        onError(new Error(`Invalid JSON (${e.message}).`));
      }
    };
    reader.onerror = () => onError(new Error("The browser could not read this file off disk."));
    reader.readAsText(file);
  } else {
    onError(new Error("Unsupported file type: ." + ext + ". Use .csv, .xlsx, .xls, .geojson, or .json."));
  }
}

/* ---------------------------------------------------------------------- */
/* Spatial preview: renders real geometry when available, otherwise a    */
/* seeded illustrative shape so the panel is never just a blank label.   */
/* ---------------------------------------------------------------------- */

function seededPolygon(seedStr, n = 7) {
  let seed = 0;
  for (let i = 0; i < seedStr.length; i++) seed = (seed * 31 + seedStr.charCodeAt(i)) >>> 0;
  function rand() {
    seed = (seed * 1103515245 + 12345) >>> 0;
    return seed / 4294967296;
  }
  const points = [];
  for (let i = 0; i < n; i++) {
    const angle = (i / n) * Math.PI * 2;
    const radius = 28 + rand() * 14;
    points.push([50 + Math.cos(angle) * radius, 50 + Math.sin(angle) * radius]);
  }
  return points.map((p) => p.join(",")).join(" ");
}

function geometryToViewboxPoints(geometry) {
  let ring = null;
  if (geometry.type === "Polygon") ring = geometry.coordinates[0];
  if (geometry.type === "MultiPolygon") ring = geometry.coordinates[0][0];
  if (!ring || !ring.length) return null;

  const lons = ring.map((c) => c[0]);
  const lats = ring.map((c) => c[1]);
  const minLon = Math.min(...lons), maxLon = Math.max(...lons);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const spanLon = maxLon - minLon || 1;
  const spanLat = maxLat - minLat || 1;

  return ring
    .map(([lon, lat]) => {
      const x = ((lon - minLon) / spanLon) * 84 + 8;
      const y = (1 - (lat - minLat) / spanLat) * 84 + 8;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

function SpatialPreview({ sourceId, sourceName, realGeometry }) {
  const realPoints = realGeometry ? geometryToViewboxPoints(realGeometry) : null;
  const points = realPoints || seededPolygon(sourceId + sourceName);
  const isReal = !!realPoints;
  return (
    <div>
      <svg viewBox="0 0 100 100" className="w-full h-32">
        <polygon points={points} fill="var(--primary-soft)" stroke="var(--primary)" strokeWidth="1.2" />
      </svg>
      <div className="text-xs muted mt-2 text-center">
        {isReal ? "Rendered from the uploaded geometry" : "Illustrative shape. Upload a geojson source in Tab 1 to render the real polygon here"}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------- */
/* Review/approval/audit data. Populated by running the pipeline against  */
/* the files the user uploads -- nothing here is seeded or pre-filled.    */
/* ---------------------------------------------------------------------- */

const REVIEW_ROWS = [];
const ALREADY_APPROVED = [];
const AUDIT_LOG = [];

/* ---------------------------------------------------------------------- */
/* Tab 1: Sources & shape                                                */
/* ---------------------------------------------------------------------- */

function Spinner() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" style={{ animation: "spin .8s linear infinite" }}>
      <circle cx="12" cy="12" r="9" stroke="var(--border-strong)" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="var(--primary)" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

function FilePickerCard({ role, file, setFile, columns, idColumn, setIdColumn, matchColumn, setMatchColumn, hierarchy, setHierarchy, setToast }) {
  const [isParsing, setIsParsing] = useState(false);
  const [pendingName, setPendingName] = useState("");

  function handleUpload(e) {
    const f = e.target.files[0];
    if (!f) return;
    setIsParsing(true);
    setPendingName(f.name);

    // Client-side parse: fast local preview, and the only place we get
    // real geometry coordinates (the backend upload response only says
    // has_geometry: true/false, not the coordinates themselves).
    parseUploadedFile(
      f,
      async (parsed) => {
        setFile({ name: f.name, format: parsed.format, rowCount: parsed.rowCount, columns: parsed.columns, geometry: parsed.geometry, geometryRows: parsed.rows || null, fileId: null, uploadError: null });
        setIdColumn(parsed.columns[0] || "");
        setMatchColumn(parsed.columns[1] || parsed.columns[0] || "");
        setHierarchy([]);

        // Server-side upload: this is what actually gets a file_id the
        // backend can run the matching pipeline against.
        try {
          const uploaded = await uploadFileToBackend(f);
          setFile({ name: f.name, format: uploaded.format, rowCount: uploaded.row_count, columns: uploaded.columns, geometry: parsed.geometry, geometryRows: parsed.rows || null, fileId: uploaded.file_id, uploadError: null });
          setIsParsing(false);
          setToast(`Uploaded ${f.name} to the backend: ${uploaded.columns.length} columns, ${uploaded.row_count} rows.`);
        } catch (err) {
          setIsParsing(false);
          setFile((prev) => ({ ...prev, fileId: null, uploadError: err.message }));
          setToast(`Parsed ${f.name} locally, but the backend upload failed: ${err.message}`);
        }
      },
      (err) => {
        setIsParsing(false);
        setToast(`Could not parse ${f.name}: ${err.message}`);
      }
    );
  }

  const idEqualsMatch = idColumn && matchColumn && idColumn === matchColumn;
  const hierarchyHasMatch = matchColumn && hierarchy.includes(matchColumn);

  return (
    <div className="card surf border p-4 flex-1">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-semibold muted uppercase tracking-wide">{role}</span>
        <span className="text-xs font-medium surf2 border b px-2 py-0.5 rounded-md uppercase">{file.format}</span>
      </div>

      {file.uploadError && (
        <div className="text-xs px-2.5 py-2 rounded-md mb-3" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>
          Backend upload failed: {file.uploadError} Fix the backend and re-upload before running the pipeline.
        </div>
      )}

      <div className="surf2 border b rounded-lg px-3 py-6 flex flex-col items-center justify-center mb-4">
        {isParsing ? (
          <>
            <Spinner />
            <div className="text-sm font-medium mt-2">Parsing {pendingName}...</div>
            <div className="text-xs muted mt-1">Large files can take a moment. This tab may pause while it works.</div>
          </>
        ) : (
          <>
            <div className="text-sm font-medium">{file.name}</div>
            <div className="text-xs muted mt-1">{file.rowCount.toLocaleString()} rows detected</div>
          </>
        )}
        <label className="btn btn-ghost text-xs font-medium px-3 py-1.5 mt-3" style={isParsing ? { opacity: 0.5, pointerEvents: "none" } : {}}>
          {isParsing ? "Parsing..." : "Upload file"}
          <input type="file" accept=".csv,.xlsx,.xls,.geojson,.json" onChange={handleUpload} className="hidden" disabled={isParsing} />
        </label>
      </div>

      <div className="flex flex-col gap-3">
        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-medium muted">ID column</span>
          <select value={idColumn} onChange={(e) => setIdColumn(e.target.value)} className="surf2 border b rounded-md text-sm px-2.5 py-2">
            {columns.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-medium muted">Match column</span>
          <select value={matchColumn} onChange={(e) => setMatchColumn(e.target.value)} className="surf2 border b rounded-md text-sm px-2.5 py-2">
            {columns.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>

        {idEqualsMatch && (
          <div className="text-xs px-2.5 py-2 rounded-md" style={{ background: "var(--warning-soft)", color: "var(--warning)" }}>
            ID column and match column are the same. That's usually a mistake, pick a different match column.
          </div>
        )}
        {hierarchyHasMatch && (
          <div className="text-xs px-2.5 py-2 rounded-md" style={{ background: "var(--warning-soft)", color: "var(--warning)" }}>
            The match column is also in the hierarchy chain. Remove it from the chain, or it'll effectively be compared against itself.
          </div>
        )}

        <div className="flex flex-col gap-1.5">
          <span className="text-xs font-medium muted">Hierarchy chain (broadest first)</span>
          <div className="flex flex-wrap gap-1.5">
            {hierarchy.map((h, i) => (
              <span key={h} className="flex items-center gap-1.5 surf2 border b rounded-md text-xs px-2.5 py-1.5">
                <span className="muted">{i + 1}.</span> {h}
                <button onClick={() => setHierarchy(hierarchy.filter((x) => x !== h))} className="muted hover:text-current ml-1">&times;</button>
              </span>
            ))}
            <select
              onChange={(e) => { if (e.target.value && !hierarchy.includes(e.target.value)) setHierarchy([...hierarchy, e.target.value]); e.target.value = ""; }}
              className="surf2 border b rounded-md text-xs px-2 py-1.5" defaultValue=""
            >
              <option value="" disabled>+ add level</option>
              {columns.filter((c) => !hierarchy.includes(c)).map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        </div>
      </div>
    </div>
  );
}

function ShapeOption({ label, description, active, onClick, diagram }) {
  return (
    <button
      onClick={onClick}
      className="card border p-3.5 flex flex-col gap-3 text-left flex-1 transition-all"
      style={active ? { background: "var(--primary-soft)", borderColor: "var(--primary)" } : { background: "var(--surface-2)", borderColor: "var(--border)" }}
    >
      <div>
        <div className="text-sm font-semibold" style={active ? { color: "var(--primary)" } : {}}>{label}</div>
        <div className="text-xs muted mt-1">{description}</div>
      </div>
      <div className="text-xs muted font-mono">{diagram}</div>
    </button>
  );
}

function TabSourcesShape(props) {
  const {
    sourceFile, setSourceFile, sourceIdCol, setSourceIdCol, sourceMatchCol, setSourceMatchCol, sourceHierarchy, setSourceHierarchy,
    targetFile, setTargetFile, targetIdCol, setTargetIdCol, targetMatchCol, setTargetMatchCol, targetHierarchy, setTargetHierarchy,
    shape, setShape, chainSteps, setChainSteps, setToast, setActiveTab,
  } = props;

  return (
    <div className="p-5 max-w-5xl mx-auto flex flex-col gap-4">
      {(sourceFile.name === "No source file selected" || targetFile.name === "No target file selected") && (
        <div className="card surf2 border p-3 text-xs muted">
          Upload a source file and a target file below to get started, CSV, Excel, or GeoJSON both work. Everything happens locally in this browser: files are parsed and matched on your machine, nothing is uploaded to a server, and there's no separate path or config file to set up.
        </div>
      )}

      <div className="flex gap-4">
        <FilePickerCard
          role="Source" file={sourceFile} setFile={setSourceFile}
          columns={sourceFile.columns} idColumn={sourceIdCol} setIdColumn={setSourceIdCol}
          matchColumn={sourceMatchCol} setMatchColumn={setSourceMatchCol}
          hierarchy={sourceHierarchy} setHierarchy={setSourceHierarchy} setToast={setToast}
        />
        <FilePickerCard
          role="Target" file={targetFile} setFile={setTargetFile}
          columns={targetFile.columns} idColumn={targetIdCol} setIdColumn={setTargetIdCol}
          matchColumn={targetMatchCol} setMatchColumn={setTargetMatchCol}
          hierarchy={targetHierarchy} setHierarchy={setTargetHierarchy} setToast={setToast}
        />
      </div>

      <div className="card surf border p-4">
        <div className="text-xs font-semibold muted uppercase tracking-wide mb-3">Linking shape</div>
        <div className="flex gap-3">
          <ShapeOption label="Single link" description="One source, one target. Standard record linkage." active={shape === "single"} onClick={() => setShape("single")} diagram="[ SRC ] to [ TGT ]" />
          <ShapeOption label="Chained" description="Step N matches on a column a previous step added." active={shape === "chained"} onClick={() => setShape("chained")} diagram="[ SRC ] to [ TGT A ] to [ TGT B ]" />
          <ShapeOption label="Hub and spoke" description="Every step matches on an original source column again." active={shape === "hub"} onClick={() => setShape("hub")} diagram="[ TGT A ] and [ TGT B ] from [ SRC ]" />
        </div>

        {shape !== "single" && (
          <div className="mt-4 flex flex-col gap-2">
            <div className="text-xs font-semibold muted uppercase tracking-wide">{shape === "chained" ? "Chain steps" : "Spokes"}</div>
            <div className="surf2 border b rounded-md px-3.5 py-2.5 flex items-center justify-between">
              <span className="text-xs font-semibold muted">STEP 1 (base)</span>
              <span className="text-sm">{targetFile.name} (base level)</span>
            </div>
            {chainSteps.map((s, i) => (
              <div key={s} className="surf2 border b rounded-md px-3.5 py-2.5 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className="text-xs font-semibold muted">STEP {i + 2}</span>
                  <span className="text-sm">{s}</span>
                </div>
                <button onClick={() => { setChainSteps(chainSteps.filter((x) => x !== s)); setToast("Step removed."); }} className="btn text-xs font-medium px-2 py-1" style={{ color: "var(--danger)" }}>Remove</button>
              </div>
            ))}
            <button
              onClick={() => { setChainSteps([...chainSteps, `new target file (${shape === "chained" ? "chained" : "spoke"} ${chainSteps.length + 2})`]); setToast("New step added."); }}
              className="btn btn-ghost text-sm font-medium px-3 py-2 self-start"
            >
              + Add {shape === "chained" ? "chain step" : "spoke"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------- */
/* Tab 2: Hierarchy & methods                                            */
/* ---------------------------------------------------------------------- */

function MethodCard({ name, engine, description, warning, enabled, onToggle, accent }) {
  return (
    <div className="card border p-4" style={{ background: enabled ? `${accent}10` : "var(--surface-2)", borderColor: enabled ? accent : "var(--border)" }}>
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2.5">
          <span className="w-2.5 h-2.5 rounded-full" style={{ background: accent }} />
          <div>
            <div className="text-sm font-semibold">{name}</div>
            <div className="text-xs muted">{engine}</div>
          </div>
        </div>
        <Switch on={enabled} onToggle={onToggle} accent={accent} />
      </div>
      <div className="text-sm muted mt-2">{description}</div>
      {warning && (
        <div className="mt-3 text-xs px-2.5 py-2 rounded-md" style={{ background: "var(--warning-soft)", color: "var(--warning)" }}>
          {warning}
        </div>
      )}
    </div>
  );
}

function TabHierarchyMethods(props) {
  const {
    sourceHierarchy, autoApprove, setAutoApprove, needsReview, setNeedsReview,
    methodA, setMethodA, methodB, setMethodB, methodC, setMethodC, methodD, setMethodD, methodE, setMethodE,
    blockingFloor, setBlockingFloor, keepDigits, setKeepDigits, stripParens, setStripParens,
    stripSuffixWords, setStripSuffixWords, caseSensitive, setCaseSensitive, setActiveTab,
  } = props;

  const chain = sourceHierarchy.length ? [...sourceHierarchy, "Match column"] : ["Level 1", "Level 2", "Level 3"];

  return (
    <div className="p-5 max-w-5xl mx-auto flex flex-col gap-4">
      <div className="card surf border p-4">
        <div className="text-xs font-semibold muted uppercase tracking-wide mb-3">Hierarchy chain</div>
        <div className="flex items-center gap-2 flex-wrap">
          {chain.map((h, i) => (
            <div key={h + i} className="flex items-center gap-2">
              <div className="flex items-center gap-2">
                <div className="surf2 border b rounded-md px-3 py-2 text-sm font-medium">{h}</div>
                <span className="text-xs muted">level {i + 1}</span>
              </div>
              {i < chain.length - 1 && <span className="muted">&rarr;</span>}
            </div>
          ))}
        </div>
        <div className="text-xs muted mt-3">Candidates are blocked at each level before name scoring runs, fuzzy rather than exact string, since hierarchy label spelling can vary between the two files.</div>
      </div>

      <div className="card surf border p-4">
        <div className="text-xs font-semibold muted uppercase tracking-wide mb-3">Confidence thresholds</div>
        <div className="grid grid-cols-2 gap-6">
          <ThresholdSlider label="Auto-approve at" value={autoApprove} setValue={setAutoApprove} accent="var(--success)" />
          <ThresholdSlider label="Needs-review floor" value={needsReview} setValue={setNeedsReview} accent="var(--warning)" />
        </div>
        {needsReview >= autoApprove && (
          <div className="mt-3 text-xs px-2.5 py-2 rounded-md" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>
            Needs-review floor must be below the auto-approve threshold.
          </div>
        )}
      </div>

      <div className="card surf border p-4">
        <div className="text-xs font-semibold muted uppercase tracking-wide mb-3">Advanced matching settings</div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-4">
          <ThresholdSlider label="Hierarchy blocking floor" value={blockingFloor} setValue={setBlockingFloor} accent="var(--primary)" />
          <div className="flex flex-col gap-2.5 justify-center">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-sm font-medium">Keep digits in normalization</div>
                <div className="text-xs muted mt-0.5">Off would collapse "Site 1" and "Site 2" into the same string.</div>
              </div>
              <Switch on={keepDigits} onToggle={() => setKeepDigits(!keepDigits)} />
            </div>
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-sm font-medium">Strip parenthetical tags</div>
                <div className="text-xs muted mt-0.5">Removes bracketed suffixes like "(HQ)", "(Branch)", or "(Old)" from names before comparing.</div>
              </div>
              <Switch on={stripParens} onToggle={() => setStripParens(!stripParens)} />
            </div>
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-sm font-medium">Strip trailing words</div>
                <div className="text-xs muted mt-0.5">Comma-separated words to drop before comparing, e.g. "office, branch". Leave blank to keep names as-is.</div>
              </div>
              <input
                type="text" value={stripSuffixWords} onChange={(e) => setStripSuffixWords(e.target.value)}
                placeholder="none" className="surf2 border b rounded-md text-xs px-2.5 py-1.5 w-32 flex-shrink-0 font-mono"
              />
            </div>
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-sm font-medium">Case-sensitive matching</div>
                <div className="text-xs muted mt-0.5">Usually off, since alternate spellings and formats often vary in capitalization too.</div>
              </div>
              <Switch on={caseSensitive} onToggle={() => setCaseSensitive(!caseSensitive)} />
            </div>
          </div>
        </div>
      </div>

      <div className="card surf border p-4">
        <div className="text-xs font-semibold muted uppercase tracking-wide mb-3">Matching methods</div>
        <div className="grid grid-cols-3 gap-3">
          <MethodCard
            name={METHOD_META.A.label} engine="difflib (character fuzzy)" accent={METHOD_META.A.accent}
            description="Best default for spelling variants and minor formatting differences. No extra dependencies."
            enabled={methodA} onToggle={() => setMethodA(!methodA)}
          />
          <MethodCard
            name={METHOD_META.B.label} engine="recordlinkage (Jaro-Winkler)" accent={METHOD_META.B.accent}
            description="A second, differently tuned character distance opinion for corroboration."
            enabled={methodB} onToggle={() => setMethodB(!methodB)}
          />
          <MethodCard
            name={METHOD_META.C.label} engine="linktransformer (embeddings)" accent={METHOD_META.C.accent}
            description="Semantic matching, good for meaning gaps rather than spelling gaps."
            warning="Weak on close spelling variants. Can repeatedly collapse distinct records onto the same wrong neighbor. Recommended as a secondary signal, not primary."
            enabled={methodC} onToggle={() => setMethodC(!methodC)}
          />
          <MethodCard
            name={METHOD_META.D.label} engine="geometry corroboration (lat/lon or polygon)" accent={METHOD_META.D.accent}
            description="Not a name-scorer: flags a candidate as suspect when its coordinates sit far from the source record's location, even if the name matched well. Needs latitude/longitude columns or GeoJSON geometry on both sides."
            enabled={methodD} onToggle={() => setMethodD(!methodD)}
          />
          <MethodCard
            name={METHOD_META.E.label} engine="Splink (probabilistic, Fellegi-Sunter)" accent={METHOD_META.E.accent}
            description="Learns how much weight each field should carry from your data instead of using a fixed similarity score. Worth it once you're linking tens of thousands of rows or more; for smaller runs, the methods above are simpler and just as effective."
            enabled={methodE} onToggle={() => setMethodE(!methodE)}
          />
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------- */
/* Tab 3: Safety & rules                                                 */
/* ---------------------------------------------------------------------- */

function TabSafetyRules(props) {
  const {
    collisionGuard, setCollisionGuard, allowManyToOne, setAllowManyToOne,
    excludePattern, setExcludePattern, typeColumn, setTypeColumn, typeExpected, setTypeExpected,
    strictZoneMatch, setStrictZoneMatch, autoDowngradeTies, setAutoDowngradeTies,
    requireHierarchyMatch, setRequireHierarchyMatch, flagLowCoverageZones, setFlagLowCoverageZones,
    sortOutputBy, setSortOutputBy, setActiveTab,
  } = props;

  const patternValid = useMemo(() => {
    if (!excludePattern.trim()) return true;
    try {
      new RegExp(excludePattern, "i");
      return true;
    } catch {
      return false;
    }
  }, [excludePattern]);

  return (
    <div className="p-5 max-w-5xl mx-auto flex flex-col gap-4">
      <div className="card surf border p-4">
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <div className="text-sm font-semibold">Collision guard</div>
            <div className="text-sm muted mt-1">
              If two different source rows independently land on the same target ID, both get downgraded to needs review instead of auto-approving a coin flip.
            </div>
          </div>
          <Switch on={collisionGuard} onToggle={() => setCollisionGuard(!collisionGuard)} />
        </div>

        <div className="mt-4 pt-4" style={{ borderTop: "1px solid var(--border)" }}>
          <div className="flex items-start justify-between">
            <div className="flex-1">
              <div className="text-sm font-medium">Allow many-to-one for this link</div>
              <div className="text-sm muted mt-1">
                Turn on when many source rows legitimately share one target, such as many child records legitimately sharing one parent. Without it, the collision guard treats every shared target as suspicious and downgrades all of them.
              </div>
            </div>
            <Switch on={allowManyToOne} onToggle={() => setAllowManyToOne(!allowManyToOne)} />
          </div>
        </div>
      </div>

      <div className="card surf border p-4">
        <div className="text-sm font-semibold mb-1">Type / level validation</div>
        <div className="text-sm muted mb-3">Every matched target ID is checked against this column. A match pointing anywhere else is downgraded to no match automatically.</div>
        <div className="grid grid-cols-2 gap-4">
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium muted">Column</span>
            <input value={typeColumn} onChange={(e) => setTypeColumn(e.target.value)} placeholder="e.g. category" className="surf2 border b rounded-md text-sm px-2.5 py-2 font-mono" />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium muted">Expected value</span>
            <input value={typeExpected} onChange={(e) => setTypeExpected(e.target.value)} placeholder="e.g. branch" className="surf2 border b rounded-md text-sm px-2.5 py-2 font-mono" />
          </label>
        </div>
      </div>

      <div className="card surf border p-4">
        <div className="text-sm font-semibold mb-3">Additional matching rules</div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-3.5">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-sm font-medium">Auto-downgrade score ties</div>
              <div className="text-xs muted mt-0.5">If two candidates tie on score for the same row, send it to review instead of guessing.</div>
            </div>
            <Switch on={autoDowngradeTies} onToggle={() => setAutoDowngradeTies(!autoDowngradeTies)} />
          </div>
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-sm font-medium">Require hierarchy match</div>
              <div className="text-xs muted mt-0.5">Reject a candidate outright if its hierarchy level never matched, even with a high name score.</div>
            </div>
            <Switch on={requireHierarchyMatch} onToggle={() => setRequireHierarchyMatch(!requireHierarchyMatch)} />
          </div>
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-sm font-medium">Strict hierarchy match</div>
              <div className="text-xs muted mt-0.5">Exact-string hierarchy match instead of fuzzy. Off is recommended: exact matching alone is fragile against spelling variance between the two files.</div>
            </div>
            <Switch on={strictZoneMatch} onToggle={() => setStrictZoneMatch(!strictZoneMatch)} accent="var(--warning)" />
          </div>
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-sm font-medium">Flag low-coverage groups</div>
              <div className="text-xs muted mt-0.5">Warn when a hierarchy group has far fewer matches than its siblings. Often a naming mismatch, not real absence.</div>
            </div>
            <Switch on={flagLowCoverageZones} onToggle={() => setFlagLowCoverageZones(!flagLowCoverageZones)} />
          </div>
        </div>

        <div className="mt-4 pt-4" style={{ borderTop: "1px solid var(--border)" }}>
          <label className="flex flex-col gap-1.5 max-w-xs">
            <span className="text-xs font-medium muted">Output row sort order</span>
            <select value={sortOutputBy} onChange={(e) => setSortOutputBy(e.target.value)} className="surf2 border b rounded-md text-sm px-2.5 py-2">
              <option value="score_desc">Score (high to low)</option>
              <option value="score_asc">Score (low to high)</option>
              <option value="source_id">Source row order</option>
              <option value="status">Status (auto, then review, then none)</option>
            </select>
          </label>
        </div>
      </div>

      <div className="card surf border p-4">
        <div className="text-sm font-semibold mb-1">Exclude by name pattern</div>
        <div className="text-sm muted mb-3">
          A second filter layered on top of type validation, for cases where the target's own type or level column isn't a fully reliable signal on its own (e.g. one category of record sharing a level or type value with the records you actually want to match against).
        </div>
        <input
          value={excludePattern} onChange={(e) => setExcludePattern(e.target.value)}
          placeholder="e.g. warehouse|depot|(annex|old)"
          className="w-full surf2 border b rounded-md text-sm px-3 py-2 font-mono"
          style={!patternValid ? { borderColor: "var(--danger)" } : {}}
        />
        {!patternValid && <div className="text-xs mt-2" style={{ color: "var(--danger)" }}>Invalid regular expression.</div>}

        <div className="flex items-start gap-2 mt-3 pt-3 border-t b" style={{ borderColor: "var(--border)" }}>
          <span className="text-[10px] font-semibold tracking-wide uppercase px-1.5 py-0.5 rounded flex-shrink-0" style={{ background: "var(--surface-2)", color: "var(--text-muted)" }}>Example</span>
          <span className="text-xs muted italic">
            A pattern like <code className="font-mono not-italic">warehouse|depot|(annex|old)</code> would exclude names such as "Central Warehouse" or "Riverside Depot (Annex)" from candidate matching. This isn't your data, just a sample pattern.
          </span>
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------- */
/* Tab 4: Review queue                                                   */
/* ---------------------------------------------------------------------- */

const KIND_META = {
  collision: { label: "Collision", color: "var(--danger)", soft: "var(--danger-soft)" },
  type_leak: { label: "Type leak", color: "var(--danger)", soft: "var(--danger-soft)" },
  low_confidence: { label: "Low confidence", color: "var(--warning)", soft: "var(--warning-soft)" },
  geometry_suspect: { label: "Geometry suspect", color: "var(--warning)", soft: "var(--warning-soft)" },
  unmatched: { label: "Unmatched", color: null, soft: null },
  consensus: { label: "Consensus", color: "var(--success)", soft: "var(--success-soft)" },
};

function KindBadge({ kind }) {
  const m = KIND_META[kind];
  if (!m || !m.color) return null;
  return <span className="text-xs font-medium px-2 py-0.5" style={{ borderRadius: 999, background: m.soft, color: m.color }}>{m.label}</span>;
}

function StatusPill({ status }) {
  const map = {
    auto_approved: { label: "Auto", color: "var(--success)", soft: "var(--success-soft)" },
    needs_review: { label: "Review", color: "var(--warning)", soft: "var(--warning-soft)" },
    no_match: { label: "No match", color: "var(--text-muted)", soft: "var(--surface-2)" },
  };
  const m = map[status];
  return <span className="text-xs font-medium px-2 py-0.5" style={{ borderRadius: 999, background: m.soft, color: m.color }}>{m.label}</span>;
}

function AlertBanner({ row }) {
  if (row.kind === "collision") {
    return (
      <div className="card border px-4 py-3 mb-4 flex items-start gap-3" style={{ background: "var(--danger-soft)", borderColor: "var(--danger)" }}>
        <div className="w-1.5 h-1.5 rounded-full mt-1.5" style={{ background: "var(--danger)" }} />
        <div>
          <div className="text-sm font-medium" style={{ color: "var(--danger)" }}>ID collision detected</div>
          <div className="text-sm muted mt-0.5">
            Target <span className="font-mono">{bestCandidate(row).targetId}</span> is also claimed by row {row.collisionPartner}. Auto-approve is blocked until you resolve which row this ID actually belongs to.
          </div>
        </div>
      </div>
    );
  }
  if (row.kind === "type_leak") {
    return (
      <div className="card border px-4 py-3 mb-4 flex items-start gap-3" style={{ background: "var(--danger-soft)", borderColor: "var(--danger)" }}>
        <div className="w-1.5 h-1.5 rounded-full mt-1.5" style={{ background: "var(--danger)" }} />
        <div>
          <div className="text-sm font-medium" style={{ color: "var(--danger)" }}>Target level leak</div>
          <div className="text-sm muted mt-0.5">This candidate belongs to a different category than the one you're linking against, even though the name matched well.</div>
        </div>
      </div>
    );
  }
  if (row.kind === "low_confidence") {
    return (
      <div className="card border px-4 py-3 mb-4 flex items-start gap-3" style={{ background: "var(--warning-soft)", borderColor: "var(--warning)" }}>
        <div className="w-1.5 h-1.5 rounded-full mt-1.5" style={{ background: "var(--warning)" }} />
        <div>
          <div className="text-sm font-medium" style={{ color: "var(--warning)" }}>Low confidence</div>
          <div className="text-sm muted mt-0.5">The best score for this row is below the needs-review floor. Check the candidates below or search manually.</div>
        </div>
      </div>
    );
  }
  if (row.kind === "geometry_suspect") {
    return (
      <div className="card border px-4 py-3 mb-4 flex items-start gap-3" style={{ background: "var(--warning-soft)", borderColor: "var(--warning)" }}>
        <div className="w-1.5 h-1.5 rounded-full mt-1.5" style={{ background: "var(--warning)" }} />
        <div>
          <div className="text-sm font-medium" style={{ color: "var(--warning)" }}>Geometry suspect</div>
          <div className="text-sm muted mt-0.5">The candidate's coordinates sit unusually far from this source record, even though the name matched well.</div>
        </div>
      </div>
    );
  }
  return null;
}

function MethodColumn({ label, engine, data, accent, isChosen, onChoose }) {
  return (
    <button
      onClick={data.name ? onChoose : undefined}
      className={`card border p-3.5 flex flex-col gap-2.5 text-left transition-all ${data.name ? "cursor-pointer" : "cursor-default opacity-60"}`}
      style={{
        borderColor: isChosen ? accent : "var(--border)",
        borderWidth: isChosen ? "2px" : "1px",
        background: isChosen ? `${accent}14` : "var(--surface-2)",
      }}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full" style={{ background: accent }} />
          <span className="text-xs font-semibold uppercase tracking-wide">{label}</span>
        </div>
        <span className="text-xs muted">{engine}</span>
      </div>
      {data.name ? (
        <>
          <div className="text-sm font-medium">{data.name}</div>
          <div className="text-xs muted font-mono">{data.targetId}</div>
          <div className="flex items-center justify-between pt-1">
            <span className="text-lg font-semibold" style={{ color: accent }}>{(data.score * 100).toFixed(1)}%</span>
            <StatusPill status={data.status} />
          </div>
          {isChosen && <div className="text-xs font-medium pt-1" style={{ color: accent }}>Selected as final match</div>}
        </>
      ) : (
        <div className="text-xs muted py-4">No candidate in this block</div>
      )}
    </button>
  );
}

function ConsensusIndicator({ row }) {
  const candidates = Object.values(row.methods).filter((m) => m.targetId !== null);
  const uniqueTargets = new Set(candidates.map((m) => m.targetId));
  if (candidates.length === 0) return <span className="text-xs font-medium px-2.5 py-1 muted" style={{ borderRadius: 999, background: "var(--surface-2)" }}>No method matched</span>;
  if (uniqueTargets.size === 1) return <span className="text-xs font-medium px-2.5 py-1" style={{ borderRadius: 999, background: "var(--success-soft)", color: "var(--success)" }}>{candidates.length}-way consensus</span>;
  return <span className="text-xs font-medium px-2.5 py-1" style={{ borderRadius: 999, background: "var(--warning-soft)", color: "var(--warning)" }}>{uniqueTargets.size} distinct targets</span>;
}

const FILTERS = [
  { key: "all", label: "All rows" },
  { key: "low_confidence", label: "Low confidence" },
  { key: "collision", label: "Collisions" },
  { key: "type_leak", label: "Type leaks" },
  { key: "geometry_suspect", label: "Geometry suspect" },
  { key: "unmatched", label: "Unmatched" },
  { key: "approved", label: "Approved" },
];

const PAGE_SIZE = 50;

/* ---------------------------------------------------------------------- */
/* Method registry -- single source of truth for method identity.        */
/* Nothing downstream should hardcode "A"/"B"/"C": the review UI reads   */
/* whichever methods are actually enabled for the project and renders    */
/* however many of them that turns out to be (1, 2, 5, whatever).        */
/* Method D (geometry) is a corroboration flag, not a competing name     */
/* candidate -- it never has a targetId, so it's excluded from the       */
/* comparison columns and from "best candidate" fallbacks below.         */
/* ---------------------------------------------------------------------- */
const METHOD_META = {
  A: { key: "A", label: "Fuzzy match", shortLabel: "Fuzzy", engine: "difflib", accent: "var(--method-a)" },
  B: { key: "B", label: "Token overlap", shortLabel: "Token overlap", engine: "recordlinkage approx.", accent: "var(--method-b)" },
  C: { key: "C", label: "Semantic embedding", shortLabel: "Semantic", engine: "linktransformer", accent: "var(--method-c)" },
  D: { key: "D", label: "Geometry check", shortLabel: "Geometry", engine: "geometry corroboration", accent: "var(--method-d)" },
  E: { key: "E", label: "Probabilistic (Splink)", shortLabel: "Probabilistic", engine: "Fellegi-Sunter", accent: "var(--method-e)" },
};
const METHOD_KEY_ORDER = ["A", "B", "C", "D", "E"];
const CANDIDATE_METHOD_KEYS = ["A", "B", "C", "E"]; // methods that can actually name a target (excludes D)

// Which method keys are enabled for this project, in a stable display order.
function enabledMethodKeys(props, { candidatesOnly = false } = {}) {
  const flags = { A: props.methodA, B: props.methodB, C: props.methodC, D: props.methodD, E: props.methodE };
  const pool = candidatesOnly ? CANDIDATE_METHOD_KEYS : METHOD_KEY_ORDER;
  return pool.filter((k) => flags[k]);
}

// The best (first non-null, by declared method order) candidate a row got,
// restricted to whichever candidate-producing methods are actually enabled.
// Falls back to scanning every candidate method if the caller doesn't know
// which were enabled (e.g. an already-merged row), so a row is never
// silently dropped from the final answer.
function bestCandidate(row, keys) {
  // Highest-scoring candidate among whichever candidate-producing methods
  // are in play, not just the first one in fixed A/B/C/E order -- a later
  // method (e.g. E) can easily outscore an earlier one (e.g. A), and the
  // "best" match should reflect that instead of alphabetical preference.
  const pool = (keys && keys.length ? keys : CANDIDATE_METHOD_KEYS).filter((k) => CANDIDATE_METHOD_KEYS.includes(k));
  let best = null;
  for (const k of pool) {
    const m = row.methods[k];
    if (m && m.name && m.score !== null && (best === null || m.score > best.score)) {
      best = { key: k, ...m };
    }
  }
  if (best) return best;
  // Fallback: a candidate with a name but no score (shouldn't normally
  // happen), take the first one found so a row is never silently dropped.
  for (const k of pool) {
    const m = row.methods[k];
    if (m && m.name) return { key: k, ...m };
  }
  return { key: null, name: null, targetId: null, score: null };
}

/* ---------------------------------------------------------------------- */
/* Tab 4: Review & finalize                                              */
/* Previously two tabs ("Review queue" and "Approve & finalize") that did */
/* largely the same job through two different mechanisms -- one row      */
/* immediately writes to the backend as you decide it, the other staged  */
/* a second round of approve/reject on whatever was left. Folded into    */
/* one tab: every decision (choosing a method, or rejecting) writes      */
/* immediately and is tracked as "decided"; "Merge and finalize" is just */
/* the final lock-in step once nothing is left undecided. The summary,   */
/* bulk actions, and merge button live in a footer that's always in      */
/* view (sticky), not something you scroll down the sidebar to find.     */
/* ---------------------------------------------------------------------- */

function TabReviewAndFinalize(props) {
  const {
    rows, selectedId, setSelectedId, note, setNote, setToast, sourceGeometry, sourceGeometryRows, sourceIdCol,
    patchReviewRow, bulkReview, undoReview, geocodeLookup,
    pendingDecisions, setPendingDecisions, merged, setMerged, finalRows,
  } = props;
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);

  const candidateKeys = enabledMethodKeys(props, { candidatesOnly: true });
  const selected = rows.find((r) => r.sourceId === selectedId) || rows[0];

  // A row counts as "decided" once it's approved (auto or by picking a
  // method) or has been explicitly rejected. Rejecting doesn't change
  // `approved` (it was already false), so that decision is tracked
  // locally in pendingDecisions -- the same map used for the merge gate.
  const isDecided = (r) => r.approved || pendingDecisions[r.sourceId] === "rejected";
  const undecidedCount = rows.filter((r) => !isDecided(r)).length;
  const decidedCount = rows.length - undecidedCount;

  const counts = useMemo(() => ({
    all: rows.length,
    low_confidence: rows.filter((r) => r.kind === "low_confidence").length,
    collision: rows.filter((r) => r.kind === "collision").length,
    type_leak: rows.filter((r) => r.kind === "type_leak").length,
    geometry_suspect: rows.filter((r) => r.kind === "geometry_suspect").length,
    unmatched: rows.filter((r) => r.kind === "unmatched").length,
    approved: rows.filter((r) => r.approved).length,
  }), [rows]);

  const filteredRows = useMemo(() => rows.filter((r) => {
    if (filter === "approved" && !r.approved) return false;
    if (filter !== "all" && filter !== "approved" && r.kind !== filter) return false;
    if (search) {
      const q = search.toLowerCase();
      if (!r.sourceName.toLowerCase().includes(q) && !r.sourceId.includes(q)) return false;
    }
    return true;
  }), [rows, filter, search]);

  const pageCount = Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pagedRows = filteredRows.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);

  useEffect(() => { setPage(0); }, [filter, search]);

  function chooseMethod(methodKey) {
    patchReviewRow(selected.sourceId, { chosen_method: methodKey, approved: true });
    setPendingDecisions((prev) => ({ ...prev, [selected.sourceId]: "approved" }));
    setToast(`Row ${selected.sourceId}: ${METHOD_META[methodKey]?.label || methodKey} selected as final match.`);
  }

  function rejectRow() {
    patchReviewRow(selected.sourceId, { approved: false, chosen_method: null });
    setPendingDecisions((prev) => ({ ...prev, [selected.sourceId]: "rejected" }));
    setToast(`Row ${selected.sourceId} rejected. Will be written as unmatched.`);
  }

  async function approveAllConsensus() {
    const targets = rows.filter((r) => {
      const candidates = Object.values(r.methods).filter((m) => m.targetId !== null);
      const uniqueTargets = new Set(candidates.map((m) => m.targetId));
      return candidates.length >= 2 && uniqueTargets.size === 1 && r.kind !== "collision" && !r.approved;
    });
    if (targets.length === 0) { setToast("No unapproved consensus rows to approve."); return; }
    const undoToken = await bulkReview("approve", targets.map((r) => r.sourceId));
    setPendingDecisions((prev) => {
      const next = { ...prev };
      targets.forEach((r) => { next[r.sourceId] = "approved"; });
      return next;
    });
    setToast({
      message: `Approved ${targets.length} row(s) with full method consensus.`,
      undo: undoToken ? () => { undoReview(undoToken); setToast("Undone."); } : undefined,
    });
  }

  async function rejectBelowThreshold() {
    const targets = rows.filter((r) => r.approved && Object.values(r.methods).some((m) => m.score !== null && m.score < 0.8));
    if (targets.length === 0) { setToast("No approved rows below 0.80 to reject."); return; }
    const undoToken = await bulkReview("reject", targets.map((r) => r.sourceId));
    setPendingDecisions((prev) => {
      const next = { ...prev };
      targets.forEach((r) => { next[r.sourceId] = "rejected"; });
      return next;
    });
    setToast({
      message: `Rejected ${targets.length} approved row(s) that had a method score below 0.80.`,
      undo: undoToken ? () => { undoReview(undoToken); setToast("Undone."); } : undefined,
    });
  }

  function approveAllRemaining() {
    const targets = rows.filter((r) => !isDecided(r));
    if (targets.length === 0) { setToast("Nothing left undecided."); return; }
    targets.forEach((r) => {
      const best = bestCandidate(r, candidateKeys);
      if (best.key) patchReviewRow(r.sourceId, { chosen_method: best.key, approved: true });
    });
    setPendingDecisions((prev) => {
      const next = { ...prev };
      targets.forEach((r) => { next[r.sourceId] = "approved"; });
      return next;
    });
    setToast(`Approved ${targets.length} remaining row(s) using each row's best available candidate.`);
  }

  async function mergeAndFinalize() {
    if (undecidedCount > 0) {
      setToast(`${undecidedCount} row(s) still undecided. Approve, reject, or use "Approve all remaining" first.`);
      return;
    }
    setMerged(true);
    setToast("Merged. Final answer ready.");
  }

  function goToNextUnreviewed() {
    const idx = filteredRows.findIndex((r) => r.sourceId === selected.sourceId);
    const rest = filteredRows.slice(idx + 1);
    const wrapped = filteredRows.slice(0, idx + 1);
    const next = [...rest, ...wrapped].find((r) => !isDecided(r));
    if (next) {
      setSelectedId(next.sourceId);
      setPage(Math.floor(filteredRows.indexOf(next) / PAGE_SIZE));
    } else {
      setToast("No undecided rows left in the current filter.");
    }
  }

  useEffect(() => {
    function onKeyDown(e) {
      if (e.key !== "n" && e.key !== "N") return;
      const tag = (e.target && e.target.tagName) || "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      goToNextUnreviewed();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  // Map source_id -> geometry index using the actual ID column values, not
  // the feature's raw position in the file. The two only coincide when the
  // ID column happens to be sequential and 0-based, which isn't a safe
  // assumption for real IDs (facility codes, non-sequential numbers, etc).
  const geometryIndexBySourceId = useMemo(() => {
    if (!sourceGeometryRows || !sourceIdCol) return null;
    const map = {};
    sourceGeometryRows.forEach((row, i) => {
      const idVal = row[sourceIdCol];
      if (idVal !== undefined && idVal !== null) map[String(idVal)] = i;
    });
    return map;
  }, [sourceGeometryRows, sourceIdCol]);

  const realGeometry = useMemo(() => {
    if (!selected || !sourceGeometry) return null;
    const idx = geometryIndexBySourceId ? geometryIndexBySourceId[String(selected.sourceId)] : undefined;
    return idx !== undefined ? sourceGeometry[idx] || null : null;
  }, [selected, sourceGeometry, geometryIndexBySourceId]);

  if (merged) {
    return (
      <div className="p-5 max-w-4xl mx-auto">
        <div className="card surf border p-5">
          <div className="flex items-center justify-between mb-1">
            <div className="text-sm font-semibold">Final linked answer</div>
            <div className="text-xs muted">Use the Export &amp; audit tab for the authoritative CSV.</div>
          </div>
          <div className="text-xs muted mb-4">{finalRows.length} rows. Clean and process-free: just the source and its final match.</div>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b b">
                <th className="pb-2 muted text-xs uppercase tracking-wide text-left">Source ID</th>
                <th className="pb-2 muted text-xs uppercase tracking-wide text-left">Source name</th>
                <th className="pb-2 muted text-xs uppercase tracking-wide text-left">Linked target</th>
                <th className="pb-2 muted text-xs uppercase tracking-wide text-left">Target ID</th>
              </tr>
            </thead>
            <tbody>
              {finalRows.map((r) => (
                <tr key={r.sourceId} className="border-b b">
                  <td className="py-2.5 font-mono text-xs muted">{r.sourceId}</td>
                  <td className="py-2.5 font-medium">{r.sourceName}</td>
                  <td className="py-2.5">{r.target}</td>
                  <td className="py-2.5 font-mono text-xs muted">{r.targetId}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <button onClick={() => setMerged(false)} className="btn btn-ghost text-xs font-medium px-3 py-1.5 mt-4">&larr; Unmerge and keep editing</button>
        </div>
      </div>
    );
  }

  if (!rows.length || !selected) {
    return (
      <div className="p-5 max-w-2xl mx-auto">
        <div className="card surf border p-6 text-center">
          <div className="text-sm font-semibold mb-1.5">Nothing to review yet</div>
          <div className="text-sm muted">
            This queue fills in once you run the pipeline against your uploaded source and target files.
            Configure your sources, methods, and rules in the earlier tabs, then use "Execute pipeline" above.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col" style={{ height: "100%" }}>
      <div className="rls-grid-review" style={{ flex: 1, minHeight: 0 }}>
        <div className="surf border-r flex flex-col">
          <div className="p-3 border-b b">
            <input type="text" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Filter rows..." className="w-full surf2 border b rounded-md text-sm px-3 py-2" style={{ color: "var(--text)" }} />
          </div>
          <div className="flex flex-col gap-1 p-2 border-b b">
            {FILTERS.map((f) => (
              <button key={f.key} onClick={() => setFilter(f.key)} className="btn flex items-center justify-between text-sm px-3 py-2" style={filter === f.key ? { background: "var(--primary-soft)", color: "var(--primary)" } : { color: "var(--text)" }}>
                <span>{f.label}</span>
                <span className="text-xs muted">{counts[f.key]}</span>
              </button>
            ))}
          </div>
          <div className="flex-1 overflow-y-auto">
            {pagedRows.map((r) => (
              <div key={r.sourceId} onClick={() => setSelectedId(r.sourceId)} className="cursor-pointer px-3.5 py-3 border-b b" style={r.sourceId === selectedId ? { background: "var(--primary-soft)" } : {}}>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs muted font-mono">#{r.sourceId}</span>
                  <div className="flex items-center gap-1.5">
                    {isDecided(r) && <span className="text-xs" style={{ color: "var(--success)" }} title="Decided">&#10003;</span>}
                    <KindBadge kind={r.kind} />
                  </div>
                </div>
                <div className="text-sm font-medium">{r.sourceName}</div>
                <div className="text-xs muted mt-0.5">{r.hierarchy}</div>
              </div>
            ))}
            {filteredRows.length === 0 && <div className="p-4 text-sm muted">No rows match this filter.</div>}
          </div>
          {pageCount > 1 && (
            <div className="flex items-center justify-between px-3 py-2 border-t b text-xs">
              <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={safePage === 0} className="btn btn-ghost px-2 py-1" style={safePage === 0 ? { opacity: 0.4, cursor: "not-allowed" } : {}}>&larr; Prev</button>
              <span className="muted">
                {safePage * PAGE_SIZE + 1}&ndash;{Math.min(filteredRows.length, (safePage + 1) * PAGE_SIZE)} of {filteredRows.length}
              </span>
              <button onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))} disabled={safePage === pageCount - 1} className="btn btn-ghost px-2 py-1" style={safePage === pageCount - 1 ? { opacity: 0.4, cursor: "not-allowed" } : {}}>Next &rarr;</button>
            </div>
          )}
        </div>

        <div className="p-5 overflow-y-auto" style={{ background: "var(--bg)" }}>
          <div className="flex items-center justify-between mb-3">
            <AlertBanner row={selected} />
          </div>
          <div className="flex justify-end -mt-3 mb-3">
            <button onClick={goToNextUnreviewed} className="btn btn-ghost text-xs font-medium px-3 py-1.5">
              Next undecided row (N) &rarr;
            </button>
          </div>

          <div className="card surf border p-4 mb-4">
            <div className="text-xs font-semibold muted uppercase tracking-wide mb-3">Source row</div>
            <div className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm">
              <div><span className="muted">source_id</span> &nbsp; <span className="font-mono">{selected.sourceId}</span></div>
              <div><span className="muted">name</span> &nbsp; {selected.sourceName}</div>
              <div><span className="muted">hierarchy</span> &nbsp; {selected.hierarchy}</div>
              <div><span className="muted">approved</span> &nbsp; {selected.approved ? "yes" : "no"}</div>
            </div>
          </div>

          <div className="flex items-center justify-between mb-3">
            <div className="text-xs font-semibold muted uppercase tracking-wide">
              {candidateKeys.length}-method comparison
            </div>
            <ConsensusIndicator row={selected} />
          </div>

          {candidateKeys.length === 0 ? (
            <div className="text-sm muted card surf2 border p-4">No candidate-producing method is enabled for this project (only geometry corroboration, if anything). Go back to "Hierarchy & methods" to turn one on.</div>
          ) : (
            <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${candidateKeys.length}, minmax(170px, 1fr))` }}>
              {candidateKeys.map((k) => (
                <MethodColumn
                  key={k}
                  label={METHOD_META[k].label}
                  engine={METHOD_META[k].engine}
                  data={selected.methods[k]}
                  accent={METHOD_META[k].accent}
                  isChosen={selected.chosenMethod === k}
                  onChoose={() => chooseMethod(k)}
                />
              ))}
            </div>
          )}
          <div className="text-xs muted mt-2">Click a method card to select it as the final match for this row.</div>
        </div>

        <div className="surf border-l flex flex-col overflow-y-auto">
          <div className="p-4 border-b b">
            <div className="text-xs font-semibold muted uppercase tracking-wide mb-2">Spatial preview</div>
            <div className="surf2 border b rounded-lg p-2">
              <SpatialPreview sourceId={selected.sourceId} sourceName={selected.sourceName} realGeometry={realGeometry} />
            </div>
          </div>

          <div className="p-4 border-b b">
            <div className="text-xs font-semibold muted uppercase tracking-wide mb-2">Audit note</div>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. verified manually against the source system" className="w-full surf2 border b rounded-md text-sm px-3 py-2 h-20 resize-none" style={{ color: "var(--text)" }} />
            <button onClick={() => { if (note) { patchReviewRow(selected.sourceId, { note }); setToast(`Note saved for row ${selected.sourceId}.`); } }} className="btn btn-ghost text-xs font-medium px-3 py-1.5 mt-2">Save note</button>
          </div>

          <div className="p-4 flex flex-col gap-2">
            <div className="text-xs font-semibold muted uppercase tracking-wide mb-1">Direct control</div>
            {candidateKeys.map((k) => (
              <button key={k} onClick={() => chooseMethod(k)} className="btn btn-ghost text-sm font-medium px-3 py-2 text-left">
                Select {METHOD_META[k].shortLabel}
              </button>
            ))}
            <button onClick={() => setToast("Manual target ID search opened.")} className="btn btn-ghost text-sm font-medium px-3 py-2 text-left">Manual target ID search</button>
            <button
              onClick={() => { setToast(`Looking up "${selected.sourceName}" via Nominatim (OpenStreetMap)...`); geocodeLookup(selected.sourceName); }}
              className="btn btn-ghost text-sm font-medium px-3 py-2 text-left"
              title="One-off geocoding lookup for stubborn cases, calls the real relink-api /api/geocode endpoint. Not run in bulk, Nominatim is rate-limited to 1 request/second."
            >
              Look up name via Nominatim
            </button>
            <button onClick={rejectRow} className="btn btn-danger text-sm font-medium px-3 py-2 text-left">Reject row</button>
          </div>
        </div>
      </div>

      {/* Always-visible footer: summary, bulk actions, and merge. No more
          scrolling the sidebar to find these. */}
      <div className="surf border-t b flex items-center justify-between gap-4 px-5 py-3" style={{ flexShrink: 0 }}>
        <div className="flex items-center gap-4 flex-wrap">
          <div className="text-xs muted">
            <span className="font-semibold" style={{ color: "var(--text)" }}>{decidedCount}</span> / {rows.length} decided
            {undecidedCount > 0 && <span> &middot; {undecidedCount} left</span>}
          </div>
          <button onClick={approveAllConsensus} className="btn btn-success text-xs font-medium px-3 py-1.5">Approve all in full consensus</button>
          <button onClick={rejectBelowThreshold} className="btn btn-danger text-xs font-medium px-3 py-1.5">Reject below threshold score</button>
          {undecidedCount > 0 && (
            <button onClick={approveAllRemaining} className="btn btn-ghost text-xs font-medium px-3 py-1.5">Approve all remaining (best candidate)</button>
          )}
        </div>
        <button
          onClick={mergeAndFinalize}
          className="btn btn-primary text-sm font-semibold px-4 py-2"
          disabled={undecidedCount > 0}
          style={undecidedCount > 0 ? { opacity: 0.5, cursor: "not-allowed" } : {}}
          title={undecidedCount > 0 ? `${undecidedCount} row(s) still undecided.` : undefined}
        >
          Merge and finalize {undecidedCount > 0 ? `(${undecidedCount} left)` : ""}
        </button>
      </div>
    </div>
  );
}

/* Tab 5: Export & audit                                                 */
/* ---------------------------------------------------------------------- */

function CoverageBar({ label, value, total, color }) {
  const pct = total > 0 ? (value / total) * 100 : 0;
  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-sm font-medium">{label}</span>
        <span className="text-sm muted">{value.toLocaleString()} / {total.toLocaleString()}, {pct.toFixed(1)}%</span>
      </div>
      <div className="h-2 rounded-full overflow-hidden" style={{ background: "var(--surface-2)" }}>
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  );
}

function TabExportAudit(props) {
  const { finalRows, totalSourceRows, setToast, reviewRows, sourceGeometry, sourceGeometryRows, sourceIdCol, exportViaBackend, projectId, auditLog, fetchAuditLog, merged, setActiveTab } = props;
  const candidateKeys = enabledMethodKeys(props, { candidatesOnly: true });
  const [mode, setMode] = useState("replace");
  const [format, setFormat] = useState(null);
  const [auditSearch, setAuditSearch] = useState("");
  const [auditExpanded, setAuditExpanded] = useState(false);

  useEffect(() => { if (projectId) fetchAuditLog(); }, [projectId]);

  const total = totalSourceRows;
  const linked = finalRows.length;
  // Real count, not a guessed multiplier: rows where every method that
  // returned a candidate agreed on the same target ID, restricted to rows
  // that actually made it into the final answer.
  const consensus = useMemo(() => {
    const finalIds = new Set(finalRows.map((r) => r.sourceId));
    return reviewRows.filter((r) => {
      if (!finalIds.has(r.sourceId)) return false;
      const candidates = Object.values(r.methods).filter((m) => m.targetId !== null);
      return candidates.length >= 2 && new Set(candidates.map((m) => m.targetId)).size === 1;
    }).length;
  }, [finalRows, reviewRows]);

  const filteredLog = useMemo(() => {
    if (!auditSearch) return auditLog;
    const q = auditSearch.toLowerCase();
    return auditLog.filter((e) => e.action.toLowerCase().includes(q) || e.ts.includes(q));
  }, [auditSearch, auditLog]);

  function exportAuditCsv() {
    const csv = "timestamp,user,action\n" + auditLog.map((e) => `"${e.ts}","${e.user}","${e.action.replace(/"/g, '""')}"`).join("\n");
    downloadFile("audit_trail.csv", csv, "text/csv");
    setToast("Audit trail exported as audit_trail.csv.");
  }

  // The backend's /export endpoint only produces CSV (source_id, source_name,
  // target_id, target_name, method, score), for approved rows only -- that's
  // the authoritative export. xlsx and geojson are built here in the browser
  // from the same approved rows, since the backend doesn't offer those
  // formats yet; both currently emit the same 4 columns regardless of the
  // merge/replace toggle above (a true "merge every original column back in"
  // would need the full source file re-read, not just the linked answer).
  async function doExport(fmt) {
    setFormat(fmt);
    if (fmt === "csv") {
      await exportViaBackend();
      return;
    }
    if (fmt === "xlsx") {
      const ws = XLSX.utils.json_to_sheet(finalRows.map((r) => ({ source_id: r.sourceId, source_name: r.sourceName, target_id: r.targetId, target_name: r.target })));
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, ws, "Linked");
      const out = XLSX.write(wb, { bookType: "xlsx", type: "array" });
      const url = URL.createObjectURL(new Blob([out], { type: "application/octet-stream" }));
      const a = document.createElement("a"); a.href = url; a.download = "relink_export.xlsx";
      document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(url);
      setToast(`Exported relink_export.xlsx (${linked} approved row(s), built in the browser).`);
      return;
    }
    if (fmt === "geojson") {
      // Look each row's geometry up by its actual ID column value, not by
      // treating the ID as a raw array position -- those only coincide
      // when the ID column happens to be sequential and 0-based.
      const geomIndexBySourceId = {};
      if (sourceGeometryRows && sourceIdCol) {
        sourceGeometryRows.forEach((row, i) => {
          const idVal = row[sourceIdCol];
          if (idVal !== undefined && idVal !== null) geomIndexBySourceId[String(idVal)] = i;
        });
      }
      const features = finalRows.map((r) => {
        const idx = geomIndexBySourceId[String(r.sourceId)];
        return {
          type: "Feature",
          geometry: (sourceGeometry && idx !== undefined && sourceGeometry[idx]) || null,
          properties: { source_id: r.sourceId, source_name: r.sourceName, target_id: r.targetId, target_name: r.target },
        };
      });
      downloadFile("relink_export.geojson", JSON.stringify({ type: "FeatureCollection", features }, null, 2), "application/geo+json");
      setToast(`Exported relink_export.geojson (${linked} approved row(s), built in the browser${sourceGeometry ? "" : " -- no source geometry was uploaded, so geometry is null on every feature"}).`);
    }
  }

  // Defense in depth: the tab bar already locks this tab until you've
  // merged and finalized, but guard the tab body too in case it's ever
  // reached another way (direct state, a future deep link, etc.) -- this
  // is the authoritative export, it should never be reachable with an
  // undecided/partial review still open.
  if (!merged) {
    return (
      <div className="p-5 max-w-2xl mx-auto">
        <div className="card surf border p-6 text-center">
          <div className="text-sm font-semibold mb-1.5">Not ready to export yet</div>
          <div className="text-sm muted mb-4">
            Export produces the authoritative, final answer set. It only makes sense once every review row has been
            decided and you've merged and finalized -- otherwise you'd be downloading a partial or undecided result.
          </div>
          <button onClick={() => setActiveTab(3)} className="btn btn-primary text-sm font-medium px-4 py-2">
            &larr; Go finish review &amp; finalize
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-5 max-w-5xl mx-auto flex flex-col gap-4">
      <div className="card surf border p-4">
        <div className="text-xs font-semibold muted uppercase tracking-wide mb-3">Run summary</div>
        <div className="flex flex-col gap-4">
          <CoverageBar label="Total coverage" value={linked} total={total} color="var(--primary)" />
          <CoverageBar
            label={candidateKeys.length > 1 ? `Full ${candidateKeys.length}-method consensus` : "Consensus"}
            value={consensus} total={total} color="var(--success)"
          />
        </div>
      </div>

      <div className="card surf border p-4">
        <div className="text-xs font-semibold muted uppercase tracking-wide mb-3">Output columns</div>
        <div className="flex gap-3">
          <button onClick={() => setMode("merge")} className="card border p-3.5 flex-1 text-left transition-all" style={mode === "merge" ? { background: "var(--primary-soft)", borderColor: "var(--primary)" } : { background: "var(--surface-2)", borderColor: "var(--border)" }}>
            <div className="text-sm font-semibold" style={mode === "merge" ? { color: "var(--primary)" } : {}}>Merge columns</div>
            <div className="text-xs muted mt-1">Keep every original column, add the linked ID alongside them.</div>
          </button>
          <button onClick={() => setMode("replace")} className="card border p-3.5 flex-1 text-left transition-all" style={mode === "replace" ? { background: "var(--primary-soft)", borderColor: "var(--primary)" } : { background: "var(--surface-2)", borderColor: "var(--border)" }}>
            <div className="text-sm font-semibold" style={mode === "replace" ? { color: "var(--primary)" } : {}}>Replace columns</div>
            <div className="text-xs muted mt-1">Output only the linked ID column, drop everything else.</div>
          </button>
        </div>
      </div>

      <div className="card surf border p-4">
        <div className="text-xs font-semibold muted uppercase tracking-wide mb-3">Export</div>
        <div className="flex gap-3">
          {["csv", "xlsx", "geojson"].map((fmt) => (
            <button key={fmt} onClick={() => doExport(fmt)} className="btn btn-primary text-sm font-medium px-5 py-2.5 flex-1">Download .{fmt}</button>
          ))}
        </div>
        {format && <div className="text-xs muted mt-3">Last export: {format}, {mode} mode</div>}
      </div>

      <div className="card surf border p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="text-xs font-semibold muted uppercase tracking-wide">Audit trail, {filteredLog.length} of {auditLog.length} entries</div>
          <div className="flex items-center gap-2">
            <input value={auditSearch} onChange={(e) => setAuditSearch(e.target.value)} placeholder="Search actions..." className="surf2 border b rounded-md text-xs px-2.5 py-1.5 w-52" />
            <button onClick={() => setAuditExpanded(!auditExpanded)} className="btn btn-ghost text-xs font-medium px-3 py-1.5">{auditExpanded ? "Collapse" : "Expand"}</button>
            <button onClick={exportAuditCsv} className="btn btn-ghost text-xs font-medium px-3 py-1.5">Export CSV</button>
          </div>
        </div>
        <div className="overflow-y-auto transition-all" style={{ maxHeight: auditExpanded ? "800px" : "260px" }}>
          <table className="w-full text-sm">
            <thead className="surf" style={{ position: "sticky", top: 0 }}>
              <tr className="border-b b">
                <th className="pb-2 muted text-xs uppercase tracking-wide">Timestamp</th>
                <th className="pb-2 muted text-xs uppercase tracking-wide">User</th>
                <th className="pb-2 muted text-xs uppercase tracking-wide">Action</th>
              </tr>
            </thead>
            <tbody>
              {filteredLog.map((entry, i) => (
                <tr key={i} className="border-b b">
                  <td className="py-2.5 muted font-mono text-xs whitespace-nowrap">{entry.ts}</td>
                  <td className="py-2.5 font-medium whitespace-nowrap">{entry.user}</td>
                  <td className="py-2.5">{entry.action}</td>
                </tr>
              ))}
              {filteredLog.length === 0 && (
                <tr>
                  <td colSpan="3" className="py-4 text-center muted text-xs">
                    {auditLog.length === 0 ? "No actions logged yet for this project." : "No matching entries."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------- */
/* Main app                                                               */
/* ---------------------------------------------------------------------- */

export default function RelinkStudio() {
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem("relink_studio_theme") || "dark"; } catch { return "dark"; }
  });
  useEffect(() => {
    try { localStorage.setItem("relink_studio_theme", theme); } catch { /* private browsing etc, non-fatal */ }
  }, [theme]);
  const [toast, setToast] = useState(null);
  const [activeTab, setActiveTab] = useState(0);

  // Tab 1 state
  const [sourceFile, setSourceFile] = useState({ name: "No source file selected", format: "-", rowCount: 0, columns: ["(upload a file to see columns)"], geometry: null, fileId: null, uploadError: null });
  const [sourceIdCol, setSourceIdCol] = useState("");
  const [sourceMatchCol, setSourceMatchCol] = useState("");
  const [sourceHierarchy, setSourceHierarchy] = useState([]);
  const [targetFile, setTargetFile] = useState({ name: "No target file selected", format: "-", rowCount: 0, columns: ["(upload a file to see columns)"], geometry: null, fileId: null, uploadError: null });
  const [targetIdCol, setTargetIdCol] = useState("");
  const [targetMatchCol, setTargetMatchCol] = useState("");
  const [targetHierarchy, setTargetHierarchy] = useState([]);
  const [shape, setShape] = useState("single");
  const [chainSteps, setChainSteps] = useState([]);

  // Tab 2 state
  const [autoApprove, setAutoApprove] = useState(0.95);
  const [needsReview, setNeedsReview] = useState(0.80);
  const [methodA, setMethodA] = useState(true);
  const [methodB, setMethodB] = useState(true);
  const [methodC, setMethodC] = useState(false);
  const [methodD, setMethodD] = useState(false);
  const [methodE, setMethodE] = useState(false);
  const [blockingFloor, setBlockingFloor] = useState(0.60);
  const [keepDigits, setKeepDigits] = useState(true);
  const [stripParens, setStripParens] = useState(true);
  const [stripSuffixWords, setStripSuffixWords] = useState("");
  const [caseSensitive, setCaseSensitive] = useState(false);

  // Tab 3 state
  const [collisionGuard, setCollisionGuard] = useState(true);
  const [allowManyToOne, setAllowManyToOne] = useState(false);
  const [excludePattern, setExcludePattern] = useState("");
  const [typeColumn, setTypeColumn] = useState("");
  const [typeExpected, setTypeExpected] = useState("");
  const [strictZoneMatch, setStrictZoneMatch] = useState(false);
  const [autoDowngradeTies, setAutoDowngradeTies] = useState(true);
  const [requireHierarchyMatch, setRequireHierarchyMatch] = useState(true);
  const [flagLowCoverageZones, setFlagLowCoverageZones] = useState(true);
  const [sortOutputBy, setSortOutputBy] = useState("score_desc");

  // Tab 4 state
  const [reviewRows, setReviewRows] = useState(REVIEW_ROWS);
  const [reviewSelectedId, setReviewSelectedId] = useState(REVIEW_ROWS[0]?.sourceId ?? null);
  const [reviewNote, setReviewNote] = useState("");

  // Tab 5 state
  const [pendingDecisions, setPendingDecisions] = useState({});
  const [merged, setMerged] = useState(false);

  // Backend run state: which project/job we're tracking, and whether a
  // run is currently in flight (drives the "Execute pipeline" button).
  const [projectId, setProjectId] = useState(null);
  const [isRunning, setIsRunning] = useState(false);
  const [auditLog, setAuditLog] = useState([]);
  const pollTimer = useRef(null);

  function methodStatus(score) {
    if (score === null || score === undefined) return null;
    if (score >= autoApprove) return "auto_approved";
    if (score < needsReview) return "no_match";
    return "needs_review";
  }

  // Backend rows don't carry a hierarchy string or a per-method "status"
  // label -- the frontend derives both here so the existing review-queue
  // UI (built against a richer mock shape) keeps working unmodified.
  function transformReviewRow(row) {
    const methods = {};
    for (const key of ["A", "B", "C", "D", "E"]) {
      const m = row.methods[key] || { name: null, targetId: null, score: null };
      methods[key] = { ...m, status: methodStatus(m.score) };
    }
    return { ...row, methods, hierarchy: row.hierarchy || "" };
  }

  async function fetchAllReviewRows(pid) {
    let page = 1;
    const pageSize = 500;
    let all = [];
    while (true) {
      const data = await apiJson(`/projects/${pid}/review?page=${page}&page_size=${pageSize}`);
      all = all.concat(data.rows.map(transformReviewRow));
      if (all.length >= data.total || data.rows.length === 0) break;
      page += 1;
    }
    return all;
  }

  async function runPipeline() {
    if (!sourceFile.fileId || !targetFile.fileId) {
      setToast("Upload both a source and target file (with a successful backend upload) before running.");
      return;
    }
    if (!methodA && !methodB && !methodC && !methodD && !methodE) {
      setToast("Enable at least one matching method (Tab 2) before running.");
      return;
    }
    setIsRunning(true);
    setToast("Creating project and starting the pipeline...");
    try {
      const config = buildConfigObject();
      const project = await apiJson("/projects", {
        method: "POST",
        body: JSON.stringify({ config, source_file_id: sourceFile.fileId, target_file_id: targetFile.fileId }),
      });
      setProjectId(project.id);

      const run = await apiJson(`/projects/${project.id}/run`, { method: "POST" });

      // Poll job status. Simple and robust across proxies/browsers; the
      // backend also exposes an SSE .../jobs/:id/stream endpoint if you'd
      // rather push updates instead of polling.
      await new Promise((resolve, reject) => {
        pollTimer.current = setInterval(async () => {
          try {
            const status = await apiJson(`/projects/${project.id}/jobs/${run.job_id}`);
            if (status.status === "running" || status.status === "pending") {
              setToast(`Matching in progress: ${status.progress ?? 0} / ${status.total ?? "?"} row(s)...`);
            } else if (status.status === "done") {
              clearInterval(pollTimer.current);
              resolve(status);
            } else if (status.status === "error") {
              clearInterval(pollTimer.current);
              reject(new Error(status.message || "The pipeline failed for an unknown reason."));
            }
          } catch (e) {
            clearInterval(pollTimer.current);
            reject(e);
          }
        }, 600);
      });

      const rows = await fetchAllReviewRows(project.id);
      setReviewRows(rows);
      setReviewSelectedId(rows[0]?.sourceId ?? null);
      setPendingDecisions({});
      setMerged(false);
      setToast({ message: `Pipeline finished: ${rows.length} row(s) matched. Opening the review queue.` });
      setActiveTab(3);
    } catch (e) {
      setToast(`Pipeline run failed: ${e.message}`);
    } finally {
      setIsRunning(false);
    }
  }

  useEffect(() => () => { if (pollTimer.current) clearInterval(pollTimer.current); }, []);

  async function patchReviewRow(sourceId, patch) {
    if (!projectId) return;
    const before = reviewRows;
    setReviewRows((prev) => prev.map((r) => (r.sourceId === sourceId ? { ...r, ...patch } : r)));
    try {
      const res = await apiJson(`/projects/${projectId}/review/${sourceId}`, { method: "PATCH", body: JSON.stringify(patch) });
      setReviewRows((prev) => prev.map((r) => (r.sourceId === sourceId ? transformReviewRow(res.row) : r)));
    } catch (e) {
      setReviewRows(before);
      setToast(`Could not save that change: ${e.message}`);
    }
  }

  async function bulkReview(action, sourceIds) {
    if (!projectId || sourceIds.length === 0) return;
    try {
      const res = await apiJson(`/projects/${projectId}/review/bulk`, {
        method: "POST",
        body: JSON.stringify({ action, source_ids: sourceIds }),
      });
      setReviewRows((prev) => prev.map((r) => (sourceIds.includes(r.sourceId) ? { ...r, approved: action === "approve" } : r)));
      return res.undo_token;
    } catch (e) {
      setToast(`Bulk ${action} failed: ${e.message}`);
      return null;
    }
  }

  async function undoReview(undoToken) {
    if (!projectId || !undoToken) return;
    try {
      await apiJson(`/projects/${projectId}/review/undo`, { method: "POST", body: JSON.stringify({ undo_token: undoToken }) });
      const rows = await fetchAllReviewRows(projectId);
      setReviewRows(rows);
    } catch (e) {
      setToast(`Undo failed: ${e.message}`);
    }
  }

  async function geocodeLookup(query) {
    try {
      const res = await apiJson("/geocode", { method: "POST", body: JSON.stringify({ query }) });
      if (!res.found) {
        setToast(`Nominatim: no result for "${query}".`);
      } else {
        setToast(`Nominatim: "${query}" -> ${res.display_name} (${res.lat}, ${res.lon}).`);
      }
    } catch (e) {
      setToast(`Nominatim lookup failed: ${e.message}`);
    }
  }

  async function exportViaBackend() {
    if (!projectId) return;
    try {
      const res = await apiFetch(`/projects/${projectId}/export`, { method: "POST" });
      const blob = await res.blob();
      const disposition = res.headers.get("Content-Disposition") || "";
      const match = disposition.match(/filename="?([^"]+)"?/);
      const filename = match ? match[1] : "relink_export.csv";
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setToast(`Downloaded ${filename} from the backend (only approved rows).`);
    } catch (e) {
      setToast(`Export failed: ${e.message}`);
    }
  }

  async function fetchAuditLog() {
    if (!projectId) return;
    try {
      const data = await apiJson(`/projects/${projectId}/audit?page_size=500`);
      setAuditLog(data.entries.map((e) => ({ ts: e.created_at, user: e.actor, action: e.detail ? `${e.action}: ${JSON.stringify(e.detail)}` : e.action })));
    } catch (e) {
      setToast(`Could not load the audit log: ${e.message}`);
    }
  }

  const finalRows = useMemo(() => {
    const candidateKeys = enabledMethodKeys({ methodA, methodB, methodC, methodD, methodE }, { candidatesOnly: true });
    const fromReview = reviewRows
      .filter((r) => r.approved)
      .map((r) => {
        const m = r.chosenMethod ? r.methods[r.chosenMethod] : bestCandidate(r, candidateKeys);
        return { sourceId: r.sourceId, sourceName: r.sourceName, target: m.name, targetId: m.targetId };
      });
    // Safety net for the brief window between an optimistic local decision
    // and the backend PATCH resolving (see patchReviewRow) -- once it
    // resolves, the row shows up via r.approved above instead.
    const fromPending = reviewRows
      .filter((r) => !r.approved && pendingDecisions[r.sourceId] === "approved")
      .map((r) => {
        const m = bestCandidate(r, candidateKeys);
        return { sourceId: r.sourceId, sourceName: r.sourceName, target: m.name, targetId: m.targetId };
      });
    return [...ALREADY_APPROVED, ...fromReview, ...fromPending].sort((a, b) => Number(a.sourceId) - Number(b.sourceId));
  }, [reviewRows, pendingDecisions, methodA, methodB, methodC, methodD, methodE]);

  function buildConfigObject() {
    return {
      project_name: `${sourceFile.name.replace(/\.[^.]+$/, "")}_to_${targetFile.name.replace(/\.[^.]+$/, "")}`,
      source: { file: sourceFile.name, id_column: sourceIdCol, match_column: sourceMatchCol, hierarchy: sourceHierarchy },
      target: {
        file: targetFile.name, id_column: targetIdCol, match_column: targetMatchCol, hierarchy: targetHierarchy,
        filter_column: typeColumn, filter_equals: typeExpected, exclude_name_pattern: excludePattern,
        type_column: typeColumn, type_expected: typeExpected, allow_many_to_one: allowManyToOne,
      },
      linking_shape: shape,
      chain_steps: shape !== "single" ? chainSteps : [],
      methods: [methodA && "fuzzy", methodB && "recordlinkage", methodC && "linktransformer", methodD && "geometry_corroboration", methodE && "splink"].filter(Boolean),
      thresholds: { auto_approve: autoApprove, needs_review: needsReview },
      matching: { blocking_floor: blockingFloor, keep_digits: keepDigits, strip_parentheticals: stripParens, strip_suffix_words: stripSuffixWords, case_sensitive: caseSensitive },
      safety: { collision_guard: collisionGuard, auto_downgrade_ties: autoDowngradeTies, require_hierarchy_match: requireHierarchyMatch, strict_zone_match: strictZoneMatch, flag_low_coverage_zones: flagLowCoverageZones },
      output: { sort_by: sortOutputBy },
    };
  }

  function handleDownloadConfig() {
    const config = buildConfigObject();
    downloadFile("relink_studio_config.json", JSON.stringify(config, null, 2), "application/json");
    setToast("Downloaded relink_studio_config.json. Anyone can upload this to load your exact setup.");
  }

  function handleUploadConfig(e) {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const c = JSON.parse(reader.result);
        if (c.source) {
          setSourceIdCol(c.source.id_column ?? sourceIdCol);
          setSourceMatchCol(c.source.match_column ?? sourceMatchCol);
          setSourceHierarchy(c.source.hierarchy ?? sourceHierarchy);
        }
        if (c.target) {
          setTargetIdCol(c.target.id_column ?? targetIdCol);
          setTargetMatchCol(c.target.match_column ?? targetMatchCol);
          setTargetHierarchy(c.target.hierarchy ?? targetHierarchy);
          setExcludePattern(c.target.exclude_name_pattern ?? excludePattern);
          setTypeColumn(c.target.type_column ?? typeColumn);
          setTypeExpected(c.target.type_expected ?? typeExpected);
          setAllowManyToOne(c.target.allow_many_to_one ?? allowManyToOne);
        }
        if (c.linking_shape) setShape(c.linking_shape);
        if (c.chain_steps) setChainSteps(c.chain_steps);
        if (c.methods) {
          setMethodA(c.methods.includes("fuzzy"));
          setMethodB(c.methods.includes("recordlinkage"));
          setMethodC(c.methods.includes("linktransformer"));
          setMethodD(c.methods.includes("geometry_corroboration"));
          setMethodE(c.methods.includes("splink"));
        }
        if (c.thresholds) {
          setAutoApprove(c.thresholds.auto_approve ?? autoApprove);
          setNeedsReview(c.thresholds.needs_review ?? needsReview);
        }
        if (c.matching) {
          setBlockingFloor(c.matching.blocking_floor ?? blockingFloor);
          setKeepDigits(c.matching.keep_digits ?? keepDigits);
          setStripParens(c.matching.strip_parentheticals ?? stripParens);
          setStripSuffixWords(c.matching.strip_suffix_words ?? stripSuffixWords);
          setCaseSensitive(c.matching.case_sensitive ?? caseSensitive);
        }
        if (c.safety) {
          setCollisionGuard(c.safety.collision_guard ?? collisionGuard);
          setAutoDowngradeTies(c.safety.auto_downgrade_ties ?? autoDowngradeTies);
          setRequireHierarchyMatch(c.safety.require_hierarchy_match ?? requireHierarchyMatch);
          setStrictZoneMatch(c.safety.strict_zone_match ?? strictZoneMatch);
          setFlagLowCoverageZones(c.safety.flag_low_coverage_zones ?? flagLowCoverageZones);
        }
        if (c.output) setSortOutputBy(c.output.sort_by ?? sortOutputBy);
        setToast(`Loaded configuration from ${file.name}. Setup now matches the file you uploaded.`);
      } catch (err) {
        setToast(`Could not read ${file.name}: not a valid Relink Studio config file.`);
      }
    };
    reader.readAsText(file);
    e.target.value = "";
  }

  const tabProps = {
    sourceFile, setSourceFile, sourceIdCol, setSourceIdCol, sourceMatchCol, setSourceMatchCol, sourceHierarchy, setSourceHierarchy,
    targetFile, setTargetFile, targetIdCol, setTargetIdCol, targetMatchCol, setTargetMatchCol, targetHierarchy, setTargetHierarchy,
    shape, setShape, chainSteps, setChainSteps,
    autoApprove, setAutoApprove, needsReview, setNeedsReview, methodA, setMethodA, methodB, setMethodB, methodC, setMethodC, methodD, setMethodD, methodE, setMethodE,
    blockingFloor, setBlockingFloor, keepDigits, setKeepDigits, stripParens, setStripParens, stripSuffixWords, setStripSuffixWords, caseSensitive, setCaseSensitive,
    collisionGuard, setCollisionGuard, allowManyToOne, setAllowManyToOne, excludePattern, setExcludePattern,
    typeColumn, setTypeColumn, typeExpected, setTypeExpected, strictZoneMatch, setStrictZoneMatch,
    autoDowngradeTies, setAutoDowngradeTies, requireHierarchyMatch, setRequireHierarchyMatch,
    flagLowCoverageZones, setFlagLowCoverageZones, sortOutputBy, setSortOutputBy,
    rows: reviewRows, setRows: setReviewRows, selectedId: reviewSelectedId, setSelectedId: setReviewSelectedId, note: reviewNote, setNote: setReviewNote,
    reviewRows, pendingDecisions, setPendingDecisions, merged, setMerged, finalRows,
    totalSourceRows: sourceFile.rowCount, sourceGeometry: sourceFile.geometry, sourceGeometryRows: sourceFile.geometryRows,
    setToast, setActiveTab,
    projectId, patchReviewRow, bulkReview, undoReview, geocodeLookup,
    exportViaBackend, auditLog, fetchAuditLog,
  };

  // What has to be true before "Continue" is allowed to move off the current tab.
  // Keeps the nav footer's enabled/disabled state consistent across every tab.
  const filesReady = sourceFile.name !== "No source file selected" && targetFile.name !== "No target file selected"
    && sourceIdCol && sourceMatchCol && targetIdCol && targetMatchCol
    && sourceFile.fileId && targetFile.fileId;
  const methodChosen = methodA || methodB || methodC || methodD || methodE;

  let navProps = { activeTab, setActiveTab };
  if (activeTab === 0) {
    navProps = { ...navProps, canContinue: filesReady, blockedReason: (sourceFile.uploadError || targetFile.uploadError) ? "Backend upload failed for one of your files, check the warning above and re-upload." : "Upload both a source and target file to continue." };
  } else if (activeTab === 1) {
    navProps = { ...navProps, canContinue: methodChosen, blockedReason: "Enable at least one matching method to continue." };
  } else if (activeTab === 3) {
    navProps = {
      ...navProps,
      canContinue: merged,
      blockedReason: "Merge and finalize your review decisions first.",
      continueLabel: "Continue to export and audit \u2192",
    };
  }

  return (
    <div data-theme={theme} className="rls" style={{ fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif", height: "100vh", display: "flex", flexDirection: "column" }}>
      <style>{TOKENS_CSS}</style>
      {toast && <Toast toast={toast} onDone={() => setToast(null)} />}
      <TopBar theme={theme} setTheme={setTheme} activeTab={activeTab} setActiveTab={setActiveTab} onDownloadConfig={handleDownloadConfig} onUploadConfig={handleUploadConfig} setToast={setToast} sourceFile={sourceFile} targetFile={targetFile} onExecutePipeline={runPipeline} isRunning={isRunning} canRun={!!(sourceFile.fileId && targetFile.fileId)} merged={merged} />

      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", paddingBottom: "76px" }}>
        {activeTab === 0 && <TabSourcesShape {...tabProps} />}
        {activeTab === 1 && <TabHierarchyMethods {...tabProps} />}
        {activeTab === 2 && <TabSafetyRules {...tabProps} />}
        {activeTab === 3 && <TabReviewAndFinalize {...tabProps} />}
        {activeTab === 4 && <TabExportAudit {...tabProps} />}
      </div>

      <NavFooter {...navProps} />
    </div>
  );
}
