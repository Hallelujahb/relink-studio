import { useState, useMemo, useEffect, useRef } from "react";
import Papa from "papaparse";
import * as XLSX from "xlsx";

/* ---------------------------------------------------------------------- */
/* Backend wiring                                                        */
/* Talks to the relink-api Flask backend (see ../relink-api). The base    */
/* URL defaults to whatever host this page itself was loaded from (port   */
/* 5000) rather than a hardcoded "localhost" -- that's what makes opening */
/* this frontend from a teammate's machine against a --lan backend work   */
/* without editing source. It can also be overridden from the connection  */
/* status panel in the top bar (persisted in localStorage), for cases     */
/* where the frontend and backend aren't on the same host.                */
/* ---------------------------------------------------------------------- */

function defaultApiBase() {
  if (typeof window !== "undefined" && window.location && window.location.hostname) {
    // The built app is served by the backend, so same origin. The Vite dev
    // server runs on another port and still talks to the backend on 5000.
    if (import.meta.env && import.meta.env.PROD) return `${window.location.origin}/api`;
    return `http://${window.location.hostname}:5000/api`;
  }
  return "http://localhost:5000/api";
}

const _conn = {
  base: (typeof localStorage !== "undefined" && localStorage.getItem("relink_api_base")) || defaultApiBase(),
  token: (typeof localStorage !== "undefined" && localStorage.getItem("relink_auth_token")) || null,
};

function getApiBase() { return _conn.base; }
function setApiBase(base) {
  if (base !== _conn.base) setAuthToken(null);
  _conn.base = base;
  try { localStorage.setItem("relink_api_base", base); } catch { /* private browsing, etc. */ }
}
function getAuthToken() { return _conn.token; }
function setAuthToken(token) {
  _conn.token = token;
  try {
    if (token) localStorage.setItem("relink_auth_token", token);
    else localStorage.removeItem("relink_auth_token");
  } catch { /* private browsing, etc. */ }
}

// Frontend build tag only -- stamped into downloaded configuration
// packages (see buildConfigPackage) so a config file records which UI
// version produced it. Not sent to, or checked against, the backend.
const RELINK_VERSION = "0.9.0-preview";

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function apiFetch(path, options = {}) {
  let res;
  const headers = {};
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  if (_conn.token) headers["Authorization"] = `Bearer ${_conn.token}`;
  try {
    res = await fetch(`${_conn.base}${path}`, { ...options, headers: { ...headers, ...(options.headers || {}) } });
  } catch (e) {
    throw new ApiError(
      `Could not reach the backend at ${_conn.base}${path}. Is relink-api running, and is this address reachable ` +
      `from your machine? Check the connection status in the top bar -- in LAN mode the backend's CORS ` +
      `origins also need to include this frontend's address (RELINK_CORS_ORIGINS).`
    );
  }
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body && body.error) message = body.error;
    } catch { /* non-JSON error body, keep default message */ }
    if (res.status === 401) {
      setAuthToken(null);
      message = "Authentication required (or your session expired). Sign in from the connection status panel in the top bar.";
    }
    throw new ApiError(message, res.status);
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

/* GET /health is always unauthenticated by design (see relink-api), so   */
/* this never needs a token -- it's what powers the connection-status     */
/* pill: which mode the backend is running in, and whether it requires    */
/* sign-in at all.                                                       */
async function checkBackendHealth() {
  const root = _conn.base.replace(/\/api\/?$/, "");
  let res;
  try {
    res = await fetch(`${root}/health`);
  } catch (e) {
    throw new Error(`Could not reach ${root}/health.`);
  }
  if (!res.ok) throw new Error(`Health check failed (${res.status}).`);
  return res.json(); // { status, mode: "local"|"lan", auth_required: bool }
}

async function loginToBackend(username, password) {
  return apiJson("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
}

/* ---------------------------------------------------------------------- */
/* Shared tokens and primitives                                          */
/* ---------------------------------------------------------------------- */

const TOKENS_CSS = `
  /* The whole app is one fixed-height flex column with its own internal
     scroll regions (TopBar, the active tab's content, NavFooter). If the
     surrounding page is ever allowed to scroll too -- which happens the
     moment html/body have their default margin and auto height -- you get
     two scrollbars fighting each other and the top bar visibly drifts.
     Locking the page itself down here means it can't happen regardless of
     what (if anything) the host page's own stylesheet does. */
  html, body, #root { height: 100%; margin: 0; overflow: hidden; }
  .rls[data-theme="dark"] {
    --bg: #0D0F13; --surface: #15181E; --surface-2: #1B1F27; --surface-hover: #232833;
    --border: #262B35; --border-strong: #333A47;
    --text: #E7EAEE; --text-muted: #8B93A3;
    --primary: #3B82F6; --primary-hover: #60A5FA; --primary-soft: #1B2B4D;
    --success: #22C55E; --success-soft: #12281A; --success-border: #3A7A57;
    --warning: #F59E0B; --warning-soft: #2E2411;
    --danger: #EF4444; --danger-soft: #341418; --danger-border: #93414D;
    --method-a: #3B82F6; --method-b: #14B8A6; --method-c: #A78BFA; --method-d: #FB923C; --method-e: #F43F5E;
    --method-f: #EC4899; --method-g: #EAB308; --method-h: #06B6D4;
    --success-track: var(--success); --warning-track: var(--warning); --primary-track: var(--primary);
    --shadow: 0 1px 2px rgba(0,0,0,0.4), 0 4px 12px rgba(0,0,0,0.25);
  }
  .rls[data-theme="light"] {
    /* Flat near-white SaaS theme -- no gradients, no tinted bars. Every
       surface (top bar, tab strip, bottom bar, cards) is the same
       near-white, distinguished only by thin borders, with blue used
       solely as the accent for selection/active/primary-action states. */
    --bg: #F5F7FA; --surface: #FFFFFF; --surface-2: #F7F9FC; --surface-hover: #EEF2F7;
    --border: #E3E8F0; --border-strong: #C9D3E0;
    --text: #1A1D29; --text-muted: #667085;
    --primary: #2F5FDB; --primary-hover: #2449AE; --primary-soft: #EAF1FE;
    --success: #157F3D; --success-soft: #DEEEDF; --success-border: #6E9C79;
    --warning: #B4620A; --warning-soft: #F1E4CE;
    --danger: #B91C1C; --danger-soft: #F3DBD8; --danger-border: #B9847C;
    --method-a: #2F5FDB; --method-b: #0D9488; --method-c: #9333EA; --method-d: #B4620A; --method-e: #DB2760;
    --method-f: #C2185B; --method-g: #B7860B; --method-h: #0E7490;
    --success-track: var(--success); --warning-track: var(--warning); --primary-track: var(--primary);
    --shadow: 0 1px 2px rgba(20,30,60,0.05), 0 4px 10px rgba(20,30,60,0.06);
  }
  .rls { background: var(--bg); color: var(--text); }
  .rls[data-theme="light"] .topbar-tint { background: var(--surface); }
  .rls[data-theme="dark"] .topbar-tint { background: var(--surface); }
  .rls[data-theme="light"] .chrome-bar { background: var(--surface); }
  .rls[data-theme="dark"] .chrome-bar { background: var(--surface); }
  .rls .footer-bar { background: var(--bg); }
  .rls[data-theme="dark"] .shape-active { background: var(--primary-soft); }
  .rls[data-theme="light"] .shape-active { background: var(--primary-soft); }
  .surf { background: var(--surface); border-color: var(--border); }
  .surf2 { background: var(--surface-2); border-color: var(--border); }
  .muted { color: var(--text-muted); }
  .b { border-color: var(--border); }
  .card { border-radius: 10px; box-shadow: var(--shadow); }
  /* Tailwind's default border color is a light gray, which shows up as a white
     outline around anything that has "border" but no explicit color. Pin every
     border to the theme so cards look the same everywhere. Inline styles
     (approved green, rejected red) still win over this. */
  .rls .card, .rls .border, .rls .border-b, .rls .border-t, .rls .border-l, .rls .border-r { border-color: var(--border); }
  .rls .btn-success { border-color: var(--success); }
  .rls .btn-danger { border-color: var(--danger); }
  /* relink-checkbox-theme: native checkboxes render white in dark mode. Draw our own. */
  .rls input[type="checkbox"] {
    -webkit-appearance: none; appearance: none; margin: 0; width: 16px; height: 16px; flex-shrink: 0;
    display: inline-grid; place-content: center; cursor: pointer; border-radius: 4px;
    background: var(--surface-2); border: 1.5px solid var(--text-muted);
  }
  .rls input[type="checkbox"]:hover { border-color: var(--primary); }
  .rls input[type="checkbox"]:checked { background: var(--primary); border-color: var(--primary); }
  .rls input[type="checkbox"]:checked::after {
    content: ""; width: 8px; height: 4px; border-left: 2px solid #fff; border-bottom: 2px solid #fff;
    transform: translateY(-1px) rotate(-45deg);
  }
  .rls input[type="checkbox"]:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
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
  /* Distinct "this row is approved" affirmation, separate from whichever
     method's own accent color was chosen -- always the same green glow
     regardless of which method card it's applied to, so approval reads
     as approval at a glance rather than blending into method branding. */
  .approvedGlow { animation: approvedPulse .15s ease-out; box-shadow: 0 0 0 2px var(--success), 0 0 14px 1px color-mix(in srgb, var(--success) 45%, transparent); }
  @keyframes approvedPulse { from { box-shadow: 0 0 0 0 color-mix(in srgb, var(--success) 60%, transparent); } to { box-shadow: 0 0 0 2px var(--success), 0 0 14px 1px color-mix(in srgb, var(--success) 45%, transparent); } }
  @keyframes toastIn { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: translateY(0); } }
  @keyframes rowIn { from { opacity: 0; transform: translateX(-6px); } to { opacity: 1; transform: translateX(0); } }
  /* Decide-then-vanish: a row flashes its decided color, holds just long
     enough to register, then collapses away fast. Kept snappy on purpose
     -- this fires on every approve/reject click, so it can't feel laggy. */
  .rowExiting { animation: rowExit .14s ease-in forwards; }
  @keyframes rowExit { from { opacity: 1; transform: scaleY(1); max-height: 80px; } to { opacity: 0; transform: scaleY(0.9); max-height: 0; margin-top: 0; margin-bottom: 0; padding-top: 0; padding-bottom: 0; } }
  @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
  .switch { width: 38px; height: 22px; border-radius: 999px; position: relative; transition: background-color .15s ease; cursor: pointer; flex-shrink: 0; }
  .switch-knob { width: 18px; height: 18px; border-radius: 999px; background: white; position: absolute; top: 2px; transition: left .15s ease; }
  input, textarea, select { outline: none; }
  input[type="number"]::-webkit-inner-spin-button, input[type="number"]::-webkit-outer-spin-button { -webkit-appearance: none; margin: 0; }
  input[type="number"] { -moz-appearance: textfield; }
  /* Browsers default the unfilled portion of a range slider to a fixed
     light gray track, which reads as a stray white bar in dark mode.
     Naively setting the host <input>'s own "background" does nothing once
     ::-webkit-slider-runnable-track is customized -- WebKit only paints
     that pseudo-element's own background, the host's background is never
     shown underneath it. Custom properties (unlike "background" itself)
     DO inherit into pseudo-elements, so the per-slider fill gets passed
     down as --track-fill and the pseudo-elements read it from there. */
  input[type="range"] { -webkit-appearance: none; appearance: none; height: 4px; border-radius: 999px; background: transparent; outline: none; }
  input[type="range"]::-webkit-slider-runnable-track { height: 4px; border-radius: 999px; background: var(--track-fill, var(--border)); }
  input[type="range"]::-webkit-slider-thumb { -webkit-appearance: none; width: 14px; height: 14px; border-radius: 50%; margin-top: -5px; background: var(--text); border: 2px solid var(--surface); cursor: pointer; }
  input[type="range"]::-moz-range-track { height: 4px; border-radius: 999px; background: var(--track-fill, var(--border)); }
  input[type="range"]::-moz-range-thumb { width: 14px; height: 14px; border-radius: 50%; background: var(--text); border: 2px solid var(--surface); cursor: pointer; }
  /* Light theme only: the thumb defaulting to --text (near-black) reads
     as a stray black dot on these light bars, so give it the slider's
     own accent color instead (falls back to --primary if none is set). */
  .rls[data-theme="light"] input[type="range"]::-webkit-slider-thumb { background: var(--thumb-accent, var(--primary)); }
  .rls[data-theme="light"] input[type="range"]::-moz-range-thumb { background: var(--thumb-accent, var(--primary)); }
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
    <div className="toast fixed bottom-4 left-1/2 surf card border px-4 py-2.5 text-sm z-50 flex items-center gap-3" style={{ transform: "translateX(-50%)", maxWidth: "calc(100vw - 32px)" }}>
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
  const pct = Math.round(value * 100);
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{label}</span>
        <NumberStepper value={value} setValue={setValue} min={0} max={1} step={0.01} />
      </div>
      <input
        type="range" min="0" max="1" step="0.01" value={value}
        onChange={(e) => setValue(parseFloat(e.target.value))}
        className="w-full"
        style={{
          accentColor: accent,
          // See the CSS comment above: this has to travel as a custom
          // property, not a literal `background`, or WebKit silently
          // drops it once the track pseudo-element is customized.
          "--track-fill": `linear-gradient(to right, ${accent} 0%, ${accent} ${pct}%, var(--border) ${pct}%, var(--border) 100%)`,
          "--thumb-accent": accent,
        }}
      />
    </div>
  );
}

const TAB_LABELS = ["Sources & shape", "Hierarchy & methods", "Safety & rules", "Review queue", "Approve & finalize", "Export & audit"];

function ConnectionStatus({ apiBase, onApiBaseChange, health, healthError, isChecking, onRefresh, isAuthenticated, authUser, onLogin, onLogout, onSwitchMode, onRegister }) {
  const [open, setOpen] = useState(false);
  const [editBase, setEditBase] = useState(apiBase);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loginError, setLoginError] = useState(null);
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [isRegistering, setIsRegistering] = useState(false);
  const [isSwitching, setIsSwitching] = useState(false);

  useEffect(() => { setEditBase(apiBase); }, [apiBase]);

  const needsSignIn = !!(health && health.auth_required && !isAuthenticated);
  let dotColor = "var(--text-muted)";
  let label = "Checking connection...";
  if (!isChecking) {
    if (healthError) {
      dotColor = "var(--danger)";
      label = "Backend unreachable";
    } else if (health) {
      if (health.mode === "lan") {
        dotColor = needsSignIn ? "var(--warning)" : "var(--success)";
        label = needsSignIn ? "Team sharing \u00b7 sign-in required" : health.auth_required ? `Team sharing \u00b7 ${authUser || "signed in"}` : "Team sharing \u00b7 no login";
      } else {
        dotColor = "var(--success)";
        label = "This computer only";
      }
    }
  }

  async function handleLogin(e) {
    e.preventDefault();
    setIsLoggingIn(true);
    setLoginError(null);
    try {
      await (isRegistering && onRegister ? onRegister(username, password) : onLogin(username, password));
      setPassword("");
    } catch (err) {
      setLoginError(err.message);
    } finally {
      setIsLoggingIn(false);
    }
  }

  return (
    <div style={{ position: "relative" }}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="btn btn-ghost text-xs font-medium px-3 py-1.5 flex items-center gap-1.5"
        title="Backend connection status"
      >
        <span className="w-1.5 h-1.5 rounded-full" style={{ background: dotColor }} />
        {label}
      </button>
      {open && (
        <div className="card surf border p-3" style={{ position: "absolute", top: "calc(100% + 6px)", right: 0, width: "300px", zIndex: 60 }}>
          <div className="text-xs font-semibold muted uppercase tracking-wide mb-2">Backend connection</div>

          <label className="flex flex-col gap-1 mb-2">
            <span className="text-xs muted">Backend URL</span>
            <input value={editBase} onChange={(e) => setEditBase(e.target.value)} className="surf2 border b rounded-md text-xs px-2 py-1.5" />
          </label>
          <div className="flex gap-2 mb-2">
            <button onClick={() => onApiBaseChange(editBase)} className="btn btn-ghost text-xs font-medium px-2.5 py-1 flex-1">Save &amp; reconnect</button>
            <button onClick={onRefresh} className="btn btn-ghost text-xs font-medium px-2.5 py-1">Re-check</button>
          </div>

          {healthError && (
            <div className="text-xs px-2 py-1.5 rounded-md mb-2" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>{healthError}</div>
          )}
          {health && (
            <div className="text-xs muted mb-2">
              Mode: <b>{health.mode}</b> &middot; Auth required: <b>{health.auth_required ? "yes" : "no"}</b>
              {health.mode === "lan" && (
                <div className="mt-1">Reachable from other devices on this private network. Never forward this port to the public internet.</div>
              )}
            </div>
          )}

          {health && onSwitchMode && (
            <div className="border-t b pt-2 mt-2 mb-2">
              <div className="text-xs font-semibold muted uppercase tracking-wide mb-1.5">Sharing</div>
              {health.mode === "lan" ? (
                <>
                  <div className="text-xs muted mb-2">Team sharing is on. Teammates on your private network can open Relink and must sign in. Your data still never leaves the network.</div>
                  <button
                    disabled={isSwitching}
                    onClick={async () => {
                      if (!window.confirm("Switch back to this computer only? Teammates will lose access.")) return;
                      setIsSwitching(true); await onSwitchMode("local"); setIsSwitching(false);
                    }}
                    className="btn btn-ghost text-xs font-medium px-2.5 py-1.5 w-full"
                  >{isSwitching ? "Restarting..." : "Switch to this computer only"}</button>
                </>
              ) : (
                <>
                  <div className="text-xs muted mb-2">Right now only this computer can open Relink. Team sharing lets teammates on the same private network use it too, with a login. Nothing goes to the internet.</div>
                  <button
                    disabled={isSwitching}
                    onClick={async () => {
                      if (!window.confirm("Turn on team sharing? Relink restarts (a few seconds), then anyone on your private network can reach it and must sign in. You will create the first account next.")) return;
                      setIsSwitching(true); await onSwitchMode("lan"); setIsSwitching(false);
                    }}
                    className="btn btn-ghost text-xs font-medium px-2.5 py-1.5 w-full"
                  >{isSwitching ? "Restarting..." : "Switch to team sharing (private network)"}</button>
                </>
              )}
            </div>
          )}

          {needsSignIn && (
            <form onSubmit={handleLogin} className="flex flex-col gap-1.5 mt-1">
              <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="username" autoComplete="username" className="surf2 border b rounded-md text-xs px-2 py-1.5" />
              <input value={password} onChange={(e) => setPassword(e.target.value)} placeholder="password" type="password" autoComplete="current-password" className="surf2 border b rounded-md text-xs px-2 py-1.5" />
              {loginError && <div className="text-xs" style={{ color: "var(--danger)" }}>{loginError}</div>}
              <button type="submit" disabled={isLoggingIn} className="btn btn-primary text-xs font-medium px-2.5 py-1.5">{isLoggingIn ? "Working..." : isRegistering ? "Create first account" : "Sign in"}</button>
              {onRegister && (
                <button type="button" onClick={() => { setIsRegistering(!isRegistering); setLoginError(null); }} className="text-xs muted" style={{ background: "none", border: "none", textDecoration: "underline", padding: 0, textAlign: "left" }}>
                  {isRegistering ? "I already have an account" : "No accounts yet? Create the first one"}
                </button>
              )}
            </form>
          )}
          {isAuthenticated && health?.auth_required && (
            <button onClick={onLogout} className="btn btn-ghost text-xs font-medium px-2.5 py-1 w-full">Sign out{authUser ? ` (${authUser})` : ""}</button>
          )}
        </div>
      )}
    </div>
  );
}

function TopBar({ theme, setTheme, activeTab, setActiveTab, onDownloadConfig, onUploadConfig, setToast, sourceFile, targetFile, onExecutePipeline, isRunning, canRun, merged, connection, fastMode, setFastMode, onNewProject }) {
  return (
    <div style={{ position: "sticky", top: 0, zIndex: 30, flexShrink: 0 }}>
      <div className="topbar-tint b border-b flex items-center justify-between px-5 py-2">
        <div className="flex items-center gap-3">
          <Logo />
          <span className="font-semibold text-sm tracking-tight">ReLink Studio</span>
          <span className="text-xs muted surf2 border b px-2 py-1 rounded-md">{sourceFile.name} &harr; {targetFile.name}</span>
          <button onClick={onNewProject} className="btn btn-ghost text-xs font-medium px-2.5 py-1" title="Clear the files and settings on this screen and start over. Nothing on the backend is deleted.">New project</button>
        </div>
        <div className="flex items-center gap-3">
          <span
            className="text-xs muted flex items-center gap-1.5"
            title={connection.health?.mode === "lan"
              ? "The backend is bound to this network (--lan mode) so teammates can reach it. Nothing goes to the internet or a third party."
              : "The backend only accepts connections from this machine. Nothing is uploaded anywhere."}
          >
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: "var(--success)" }} />
            {connection.health?.mode === "lan" ? "Running on your private network \u2014 no cloud, no internet" : "Runs locally, data never leaves this machine"}
          </span>
          <ConnectionStatus {...connection} />
          <button onClick={() => setTheme(theme === "dark" ? "light" : "dark")} className="btn btn-ghost text-xs font-medium px-3 py-1.5">
            {theme === "dark" ? "Light mode" : "Dark mode"}
          </button>
          <label className="btn btn-ghost text-xs font-medium px-3 py-1.5">
            Upload config
            <input type="file" accept=".json" onChange={onUploadConfig} className="hidden" />
          </label>
          <button onClick={onDownloadConfig} className="btn btn-ghost text-xs font-medium px-3 py-1.5">Download config</button>
          <div
            role="switch" aria-checked={fastMode} tabIndex={0}
            onClick={() => setFastMode(!fastMode)}
            onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); setFastMode(!fastMode); } }}
            className="text-xs font-medium flex items-center gap-2 cursor-pointer select-none"
            style={{ color: fastMode ? "var(--success)" : "var(--text-muted)" }}
            title="After the pipeline finishes, auto-approve rows with full method agreement at or above your auto-approve threshold, then drop you straight onto the short needs-attention list instead of the full checklist."
          >
            <Switch on={fastMode} onToggle={() => {}} accent="var(--success)" />
            Fast mode
          </div>
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
      <div className="surf chrome-bar border-b flex items-center px-5 py-1.5 gap-2 overflow-x-auto">
        {TAB_LABELS.map((label, i) => {
          // The export tab produces the authoritative CSV, which only makes
          // sense once review decisions are merged & finalized -- but rather
          // than making the tab itself inert beforehand, it's left clickable
          // and shows its own "Not ready to export yet" screen instead (see
          // TabExportAudit), so you can always get in to see why.
          const isExportTab = i === TAB_LABELS.length - 1;
          const locked = false;
          return (
            <div key={label} className="flex items-center gap-2">
              <div
                onClick={() => { if (locked) { setToast('Merge and finalize your review decisions first (tab "Approve & finalize").'); return; } setActiveTab(i); }}
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
    </div>
  );
}

function NavFooter({ activeTab, setActiveTab, canContinue = true, continueLabel, onContinue, blockedReason }) {
  const isFirst = activeTab === 0;
  const isLast = activeTab === TAB_LABELS.length - 1;
  if (isFirst && isLast) return null;

  // A docked, full-width bar flush against the bottom edge -- not a pair
  // of pill buttons floating with gaps around them. It sits outside the
  // scrollable tab content (see the root layout below) so it never moves.
  return (
    <div
      className="surf footer-bar border-t b flex items-center justify-between px-5 py-1.5"
      style={{ flexShrink: 0, position: "sticky", bottom: 0, zIndex: 30 }}
    >
      <div>
        {!isFirst && (
          <button onClick={() => setActiveTab(activeTab - 1)} className="btn btn-ghost text-sm font-medium px-3.5 py-1.5">
            &larr; Back
          </button>
        )}
      </div>
      {!isLast && (
        <div className="flex items-center gap-3">
          {!canContinue && blockedReason && (
            <span className="text-xs muted">{blockedReason}</span>
          )}
          <button
            onClick={() => { if (canContinue) (onContinue ? onContinue() : setActiveTab(activeTab + 1)); }}
            disabled={!canContinue}
            className="btn btn-primary text-sm font-medium px-3.5 py-1.5"
            style={!canContinue ? { opacity: 0.45, cursor: "not-allowed" } : {}}
            title={!canContinue ? blockedReason : undefined}
          >
            {continueLabel || `Continue to ${TAB_LABELS[activeTab + 1]} \u2192`}
          </button>
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------------- */
/* File parsing (real, used by Tab 1)                                    */
/* ---------------------------------------------------------------------- */

// A quick, honest heuristic, not a guarantee: scans header names for the
// obvious conventions ("id", "source_id", "facility_name"...) so a lazy
// person doesn't start from two blank dropdowns every single time. Always
// overridable, never silently trusted for anything downstream.
function guessIdColumn(columns) {
  if (!columns || !columns.length) return "";
  const lower = columns.map((c) => String(c).toLowerCase());
  const exact = lower.indexOf("id");
  if (exact !== -1) return columns[exact];
  const idLike = lower.findIndex((c) => /(^|_)id($|_)/.test(c));
  if (idLike !== -1) return columns[idLike];
  return columns[0];
}
function guessMatchColumn(columns, idGuess) {
  if (!columns || !columns.length) return "";
  const lower = columns.map((c) => String(c).toLowerCase());
  const nameLike = lower.findIndex((c, i) => columns[i] !== idGuess && /name|facility|label|title/.test(c));
  if (nameLike !== -1) return columns[nameLike];
  const fallback = columns.find((c) => c !== idGuess);
  return fallback || columns[0];
}

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
/* Spatial preview: draws the real geometry, or says there is none.      */
/*                                                                        */
/* ---------------------------------------------------------------------- */

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

function SpatialPreview({ realGeometry }) {
  const points = realGeometry ? geometryToViewboxPoints(realGeometry) : null;
  if (!points) {
    return (
      <div className="text-xs muted text-center py-6">
        No polygon to draw. Upload a GeoJSON source with polygon geometry to see it here.
      </div>
    );
  }
  return (
    <div>
      <svg viewBox="0 0 100 100" className="w-full h-32">
        <polygon points={points} fill="var(--primary-soft)" stroke="var(--primary)" strokeWidth="1.2" />
      </svg>
      <div className="text-xs muted mt-2 text-center">Rendered from the uploaded geometry</div>
    </div>
  );
}

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

const DB_PROVIDER_DEFAULT_PORTS = { postgresql: 5432, clickhouse: 8123, mysql: 3306 };

/* Pure form -- no open/close state of its own. The dropzone box in       */
/* FilePickerCard owns whether this or the regular upload view is shown,  */
/* so the two share one box instead of stacking as separate panels.       */
function DbImportForm({ onBack, onIngested, setToast }) {
  const [provider, setProvider] = useState("postgresql");
  const [host, setHost] = useState("localhost");
  const [port, setPort] = useState(DB_PROVIDER_DEFAULT_PORTS.postgresql);
  const [database, setDatabase] = useState("");
  const [schemaField, setSchemaField] = useState("");
  const [table, setTable] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [ssl, setSsl] = useState(false);
  const [rowLimit, setRowLimit] = useState(50000);
  const [isBusy, setIsBusy] = useState(false);
  const [testResult, setTestResult] = useState(null);

  useEffect(() => { setPort(DB_PROVIDER_DEFAULT_PORTS[provider] || ""); }, [provider]);

  function connConfig() {
    return {
      provider, host, port: Number(port) || null, database,
      schema: schemaField || null, table, username: username || null,
      password: password || null, ssl,
    };
  }

  async function handleTest() {
    setIsBusy(true);
    setTestResult(null);
    try {
      await apiJson("/db/test-connection", { method: "POST", body: JSON.stringify(connConfig()) });
      setTestResult({ ok: true, message: "Connected successfully." });
    } catch (err) {
      setTestResult({ ok: false, message: err.message });
    } finally {
      setIsBusy(false);
    }
  }

  async function handleIngest() {
    setIsBusy(true);
    try {
      const res = await apiJson("/db/ingest", {
        method: "POST",
        body: JSON.stringify({ ...connConfig(), row_limit: Number(rowLimit) || null }),
      });
      onIngested(res);
      setToast(
        res.truncated
          ? `Imported the first ${res.row_count.toLocaleString()} row(s) from ${provider}:${table} (row limit reached -- raise it to pull more).`
          : `Imported ${res.row_count.toLocaleString()} row(s) from ${provider}:${table}.`
      );
    } catch (err) {
      setToast(`Database import failed: ${err.message}`);
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <div style={{ width: "100%", textAlign: "left" }} className="flex flex-col gap-2">
      <button
        type="button"
        onClick={onBack}
        className="text-xs flex items-center gap-1"
        style={{ border: "none", background: "none", padding: 0, color: "var(--text-muted)", alignSelf: "flex-start" }}
      >
        &larr; Back to file upload
      </button>
      <div className="flex gap-2">
        <select value={provider} onChange={(e) => setProvider(e.target.value)} className="surf border b rounded-md text-xs px-2 py-1.5 flex-1">
          <option value="postgresql">PostgreSQL</option>
          <option value="clickhouse">ClickHouse</option>
          <option value="mysql">MySQL / MariaDB</option>
        </select>
        <label className="flex items-center gap-1.5 text-xs muted whitespace-nowrap">
          <input type="checkbox" checked={ssl} onChange={(e) => setSsl(e.target.checked)} /> SSL
        </label>
      </div>
      <div className="flex gap-2">
        <input value={host} onChange={(e) => setHost(e.target.value)} placeholder="host" className="surf border b rounded-md text-xs px-2 py-1.5 flex-1" />
        <input value={port} onChange={(e) => setPort(e.target.value)} placeholder="port" className="surf border b rounded-md text-xs px-2 py-1.5" style={{ width: "72px" }} />
      </div>
      <input value={database} onChange={(e) => setDatabase(e.target.value)} placeholder="database" className="surf border b rounded-md text-xs px-2 py-1.5" />
      <div className="flex gap-2">
        <input value={schemaField} onChange={(e) => setSchemaField(e.target.value)} placeholder={provider === "mysql" ? "schema (unused for MySQL)" : "schema (optional)"} disabled={provider === "mysql"} className="surf border b rounded-md text-xs px-2 py-1.5 flex-1" style={provider === "mysql" ? { opacity: 0.5 } : {}} />
        <input value={table} onChange={(e) => setTable(e.target.value)} placeholder="table" className="surf border b rounded-md text-xs px-2 py-1.5 flex-1" />
      </div>
      <div className="flex gap-2">
        <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="username" autoComplete="off" className="surf border b rounded-md text-xs px-2 py-1.5 flex-1" />
        <input value={password} onChange={(e) => setPassword(e.target.value)} placeholder="password" type="password" autoComplete="off" className="surf border b rounded-md text-xs px-2 py-1.5 flex-1" />
      </div>
      <label className="flex items-center gap-1.5 text-xs muted">
        Row limit
        <input value={rowLimit} onChange={(e) => setRowLimit(e.target.value)} type="number" className="surf border b rounded-md text-xs px-2 py-1 flex-1" />
      </label>
      {testResult && (
        <div
          className="text-xs px-2 py-1.5 rounded-md"
          style={{ background: testResult.ok ? "var(--success-soft)" : "var(--danger-soft)", color: testResult.ok ? "var(--success)" : "var(--danger)" }}
        >
          {testResult.message}
        </div>
      )}
      <div className="flex gap-2 mt-1">
        <button onClick={handleTest} disabled={isBusy || !host || !database} className="btn btn-ghost text-xs font-medium px-2.5 py-1.5 flex-1">Test connection</button>
        <button onClick={handleIngest} disabled={isBusy || !host || !database || !table} className="btn btn-primary text-xs font-medium px-2.5 py-1.5 flex-1">
          {isBusy ? "Importing..." : "Import table"}
        </button>
      </div>
      <div className="text-xs muted">
        Runs through the backend's own database drivers (psycopg2 / clickhouse-connect / mysql-connector-python --
        whichever match the provider above -- must be installed there; see <code>/api/connectors/capabilities</code>).
        Credentials are sent once for this request and are never stored or logged by ReLink.
      </div>
    </div>
  );
}

function FilePickerCard({ role, file, setFile, columns, idColumn, setIdColumn, matchColumn, setMatchColumn, hierarchy, setHierarchy, setToast }) {
  const [isParsing, setIsParsing] = useState(false);
  // Shared folder (RELINK_LIBRARY_DIR on the backend). null when not configured.
  const [library, setLibrary] = useState(null);
  useEffect(() => {
    let alive = true;
    apiJson("/library").then((r) => { if (alive) setLibrary(r && r.enabled && r.files.length ? r : null); }).catch(() => {});
    return () => { alive = false; };
  }, []);

  async function importFromLibrary(name) {
    setIsParsing(true);
    setPendingName(name);
    try {
      const up = await apiJson("/library/import", { method: "POST", body: JSON.stringify({ filename: name }) });
      let geometry = null, geometryRows = null;
      if (up.has_geometry) {
        const g = await apiJson(`/upload/${up.file_id}?geometry=1`);
        geometry = g.geometry || null; geometryRows = g.rows || null;
      }
      setFile({ name: up.filename, format: up.format, rowCount: up.row_count, columns: up.columns, geometry, geometryRows, fileId: up.file_id, uploadError: null });
      const guessedId = guessIdColumn(up.columns);
      setIdColumn(guessedId);
      setMatchColumn(guessMatchColumn(up.columns, guessedId));
      setHierarchy([]);
      setToast(`Loaded ${up.filename} from the shared folder: ${up.columns.length} columns, ${up.row_count} rows.`);
    } catch (err) {
      setToast(`Could not load ${name} from the shared folder: ${err.message}`);
    } finally {
      setIsParsing(false);
    }
  }
  const [pendingName, setPendingName] = useState("");
  const [isDragOver, setIsDragOver] = useState(false);
  const dragDepth = useRef(0);
  const [mode, setMode] = useState("upload"); // "upload" | "db" -- which view the box shows

  function processFile(f) {
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
        const guessedId = guessIdColumn(parsed.columns);
        setIdColumn(guessedId);
        setMatchColumn(guessMatchColumn(parsed.columns, guessedId));
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

  function handleUpload(e) {
    processFile(e.target.files[0]);
    e.target.value = "";
  }

  const ACCEPTED_EXT = [".csv", ".xlsx", ".xls", ".geojson", ".json"];
  function isAcceptedFile(f) {
    const name = (f.name || "").toLowerCase();
    return ACCEPTED_EXT.some((ext) => name.endsWith(ext));
  }

  function handleDragEnter(e) {
    e.preventDefault();
    e.stopPropagation();
    dragDepth.current += 1;
    if (!isParsing) setIsDragOver(true);
  }
  function handleDragOver(e) {
    // Required for onDrop to fire at all -- browsers reject drops on
    // elements that don't cancel dragover.
    e.preventDefault();
    e.stopPropagation();
  }
  function handleDragLeave(e) {
    e.preventDefault();
    e.stopPropagation();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setIsDragOver(false);
  }
  function handleDrop(e) {
    e.preventDefault();
    e.stopPropagation();
    dragDepth.current = 0;
    setIsDragOver(false);
    if (isParsing) return;
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (!f) return;
    if (!isAcceptedFile(f)) {
      setToast(`${f.name} isn't one of the accepted types (.csv, .xlsx, .xls, .geojson, .json).`);
      return;
    }
    setMode("upload");
    processFile(f);
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

      <div
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`surf2 border b rounded-lg px-3 flex flex-col mb-4 ${mode === "db" ? "py-4" : "py-6"}`}
        style={{
          alignItems: mode === "db" ? "stretch" : "center",
          justifyContent: mode === "db" ? "flex-start" : "center",
          borderStyle: mode === "upload" && isDragOver ? "dashed" : "solid",
          borderColor: mode === "upload" && isDragOver ? "var(--primary)" : undefined,
          background: mode === "upload" && isDragOver ? "var(--primary-soft)" : undefined,
          transition: "background-color .1s ease, border-color .1s ease",
        }}
      >
        {mode === "db" ? (
          <DbImportForm
            onBack={() => setMode("upload")}
            setToast={setToast}
            onIngested={(res) => {
              setFile({ name: res.filename, format: res.format, rowCount: res.row_count, columns: res.columns, geometry: null, geometryRows: null, fileId: res.file_id, uploadError: null });
              const guessedId = guessIdColumn(res.columns);
              setIdColumn(guessedId);
              setMatchColumn(guessMatchColumn(res.columns, guessedId));
              setHierarchy([]);
              setMode("upload");
            }}
          />
        ) : (
          <>
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
                <div className="text-xs muted mt-1">{isDragOver ? "Drop to upload" : "Drag a file here, or"}</div>
              </>
            )}
            <label className="btn btn-ghost text-xs font-medium px-3 py-1.5 mt-3" style={isParsing ? { opacity: 0.5, pointerEvents: "none" } : {}}>
              {isParsing ? "Parsing..." : (file.fileId ? "Change file" : "Upload file")}
              <input type="file" accept=".csv,.xlsx,.xls,.geojson,.json" onChange={handleUpload} className="hidden" disabled={isParsing} />
            </label>
            <button
              type="button"
              onClick={() => setMode("db")}
              className="text-xs mt-3"
              style={{ border: "none", background: "none", padding: 0, textDecoration: "underline", color: "var(--text-muted)", opacity: file.fileId ? 0.7 : 1 }}
            >
              {file.fileId ? "Or replace with a table from Postgres / ClickHouse / MySQL" : "Or import a table from Postgres / ClickHouse / MySQL"}
            </button>
            {library && (
              <select
                defaultValue="" disabled={isParsing}
                onChange={(e) => { const name = e.target.value; e.target.value = ""; if (name) importFromLibrary(name); }}
                className="surf2 border b rounded-md text-xs px-2 py-1.5 mt-3"
              >
                <option value="" disabled>Or pick from the shared folder ({library.folder})</option>
                {library.files.map((f) => <option key={f.name} value={f.name}>{f.name}</option>)}
              </select>
            )}
          </>
        )}
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
      className={`card border p-3.5 flex flex-col gap-3 text-left flex-1 transition-all${active ? " shape-active" : ""}`}
      style={active ? { borderColor: "var(--primary)" } : { background: "var(--surface-2)", borderColor: "var(--border)" }}
    >
      <div>
        <div className="text-sm font-semibold" style={active ? { color: "var(--primary)" } : {}}>{label}</div>
        <div className="text-xs muted mt-1">{description}</div>
      </div>
      <div className="text-xs muted font-mono">{diagram}</div>
    </button>
  );
}

function makeEmptyStep(index) {
  return {
    id: `step_${Date.now()}_${index}`,
    file: { name: "No file selected", format: "-", rowCount: 0, columns: ["(upload a file to see columns)"], geometry: null, geometryRows: null, fileId: null, uploadError: null },
    idCol: "", matchCol: "", hierarchy: [],
  };
}

function TabSourcesShape(props) {
  const {
    sourceFile, setSourceFile, sourceIdCol, setSourceIdCol, sourceMatchCol, setSourceMatchCol, sourceHierarchy, setSourceHierarchy,
    targetFile, setTargetFile, targetIdCol, setTargetIdCol, targetMatchCol, setTargetMatchCol, targetHierarchy, setTargetHierarchy,
    shape, setShape, chainSteps, setChainSteps, setToast, setActiveTab, expectedFiles,
  } = props;

  function addStep() {
    setChainSteps([...chainSteps, makeEmptyStep(chainSteps.length)]);
  }
  function removeStep(id) {
    setChainSteps(chainSteps.filter((s) => s.id !== id));
    setToast("Step removed.");
  }
  function updateStep(id, patch) {
    setChainSteps(chainSteps.map((s) => (s.id === id ? { ...s, ...(typeof patch === "function" ? patch(s) : patch) } : s)));
  }

  return (
    <div className="p-5 max-w-5xl mx-auto flex flex-col gap-4">
      {expectedFiles && (expectedFiles.source || expectedFiles.target || chainSteps.some((s) => s.expectedFile))
        && (!sourceFile.fileId || !targetFile.fileId || chainSteps.some((s) => s.expectedFile && !s.file.fileId)) && (
        <div className="card surf2 border p-3 text-xs">
          <div className="font-semibold mb-1">The loaded configuration expects these files</div>
          <div className="muted">
            Source: {expectedFiles.source || "not specified"} &middot; Target: {expectedFiles.target || "not specified"}
            {chainSteps.filter((s) => s.expectedFile).map((s, i) => <span key={s.id}> &middot; Step {i + 2}: {s.expectedFile}</span>)}
          </div>
          <div className="muted mt-1">Upload them below, or put them in the shared folder (RELINK_LIBRARY_DIR) and load the config again to attach them automatically.</div>
        </div>
      )}
      {(sourceFile.name === "No source file selected" || targetFile.name === "No target file selected") && (
        <div className="card surf2 border p-3 text-xs muted">
          Upload a source file and a target file below to get started, CSV, Excel, or GeoJSON both work. Files are parsed locally for the preview you see here, and also uploaded to the backend to actually run the matching pipeline.
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
          <div className="mt-4 flex flex-col gap-3">
            <div className="text-xs font-semibold muted uppercase tracking-wide">{shape === "chained" ? "Chain steps" : "Spokes"}</div>
            <div className="surf2 border b rounded-md px-3.5 py-2.5 flex items-center justify-between">
              <span className="text-xs font-semibold muted">STEP 1 (base)</span>
              <span className="text-sm">{targetFile.name} (base level, from the Target card above)</span>
            </div>
            <div className="flex gap-4 flex-wrap">
              {chainSteps.map((step, i) => (
                <div key={step.id} style={{ minWidth: 280, flex: "1 1 280px" }}>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-semibold muted">
                      STEP {i + 2}{shape === "chained" ? `, matches against Step ${i + 1}'s result` : ", matches against the original Source"}
                    </span>
                    <button onClick={() => removeStep(step.id)} className="btn text-xs font-medium px-2 py-1" style={{ color: "var(--danger)" }}>Remove</button>
                  </div>
                  <FilePickerCard
                    role={shape === "chained" ? `Step ${i + 2} target` : `Spoke ${i + 2}`}
                    file={step.file} setFile={(f) => updateStep(step.id, (s) => ({ file: typeof f === "function" ? f(s.file) : f }))}
                    columns={step.file.columns} idColumn={step.idCol} setIdColumn={(v) => updateStep(step.id, { idCol: v })}
                    matchColumn={step.matchCol} setMatchColumn={(v) => updateStep(step.id, { matchCol: v })}
                    hierarchy={step.hierarchy} setHierarchy={(v) => updateStep(step.id, (s) => ({ hierarchy: typeof v === "function" ? v(s.hierarchy) : v }))}
                    setToast={setToast}
                  />
                </div>
              ))}
            </div>
            <button onClick={addStep} className="btn btn-ghost text-sm font-medium px-3 py-2 self-start">
              + Add {shape === "chained" ? "chain step" : "spoke"}
            </button>
            <div className="text-xs muted px-1">
              This captures the full multi-step shape and sends it to the backend, but the matching engine itself only executes one step at a time so far, each step here still needs its own file uploaded and columns picked, same as Source and Target above.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------- */
/* Tab 2: Hierarchy & methods                                            */
/* ---------------------------------------------------------------------- */

function MethodCard({ name, engine, description, warning, enabled, onToggle, accent, footer }) {
  return (
    <div
      className="card border p-4"
      style={{
        background: "var(--surface)",
        borderColor: enabled ? accent : "var(--border)",
        borderLeftWidth: "4px",
        borderLeftColor: enabled ? accent : "var(--border)",
      }}
    >
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
      {footer}
    </div>
  );
}

function AddMethodCard({ onClick }) {
  return (
    <button
      onClick={onClick}
      className="card p-4 flex flex-col items-center justify-center gap-2 text-center"
      style={{ border: "1.5px dashed var(--border)", background: "transparent", minHeight: "132px" }}
    >
      <span className="text-xl muted">+</span>
      <div className="text-sm font-medium">Add your method</div>
      <div className="text-xs muted">Point at your own Python scoring script</div>
    </button>
  );
}

const METHOD_COLOR_CHOICES = [
  { key: "a", var: "var(--method-a)" }, { key: "b", var: "var(--method-b)" }, { key: "c", var: "var(--method-c)" },
  { key: "d", var: "var(--method-d)" }, { key: "e", var: "var(--method-e)" }, { key: "f", var: "var(--method-f)" },
  { key: "g", var: "var(--method-g)" }, { key: "h", var: "var(--method-h)" },
];

function AddMethodModal({ onClose, onSave, takenColors }) {
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [entryFunction, setEntryFunction] = useState("score");
  const [description, setDescription] = useState("");
  // Same color "vibe" as the built-in methods, not a generic gray -- and
  // default to whichever one nothing else is currently using yet, so two
  // methods don't accidentally look identical at a glance.
  const firstFree = METHOD_COLOR_CHOICES.find((c) => !takenColors.includes(c.var)) || METHOD_COLOR_CHOICES[0];
  const [color, setColor] = useState(firstFree.var);
  const canSave = name.trim() && path.trim();

  return (
    <div className="fixed inset-0 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.5)", zIndex: 60 }} onClick={onClose}>
      <div className="card surf border p-5" style={{ width: "440px", maxWidth: "90vw" }} onClick={(e) => e.stopPropagation()}>
        <div className="text-sm font-semibold mb-3">Add your method</div>
        <div className="flex flex-col gap-3">
          <div>
            <div className="text-xs muted mb-1">Method name</div>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. MyResolver" className="w-full surf2 border b rounded-md text-sm px-3 py-2" style={{ color: "var(--text)" }} />
          </div>
          <div>
            <div className="text-xs muted mb-1">Color</div>
            <div className="flex items-center gap-2">
              {METHOD_COLOR_CHOICES.map((c) => (
                <button
                  key={c.key} onClick={() => setColor(c.var)} title={takenColors.includes(c.var) ? "Already used by another method" : ""}
                  className="rounded-full flex-shrink-0"
                  style={{
                    width: "22px", height: "22px", background: c.var,
                    border: color === c.var ? "2px solid var(--text)" : "2px solid transparent",
                    boxShadow: color === c.var ? "0 0 0 2px var(--surface)" : "none",
                    opacity: takenColors.includes(c.var) && color !== c.var ? 0.35 : 1,
                  }}
                />
              ))}
            </div>
          </div>
          <div>
            <div className="text-xs muted mb-1">Path to your Python file</div>
            <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/methods/my_resolver.py" className="w-full surf2 border b rounded-md text-sm px-3 py-2 font-mono" style={{ color: "var(--text)" }} />
          </div>
          <div>
            <div className="text-xs muted mb-1">Entry function (must accept source/candidate rows + params, return scores)</div>
            <input value={entryFunction} onChange={(e) => setEntryFunction(e.target.value)} className="w-full surf2 border b rounded-md text-sm px-3 py-2 font-mono" style={{ color: "var(--text)" }} />
          </div>
          <div>
            <div className="text-xs muted mb-1">Description (optional)</div>
            <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} className="w-full surf2 border b rounded-md text-sm px-3 py-2" style={{ color: "var(--text)" }} />
          </div>
          <div className="text-xs muted">
            Not executed yet: the backend does not load or run custom scripts. This entry is stored in your configuration package (path only, never the script contents) so it can be reviewed and wired up deliberately later.
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-4">
          <button onClick={onClose} className="btn btn-ghost text-sm font-medium px-3 py-1.5">Cancel</button>
          <button
            onClick={() => canSave && onSave({ name: name.trim(), path: path.trim(), entryFunction: entryFunction.trim() || "score", description: description.trim(), color })}
            disabled={!canSave}
            className="btn btn-primary text-sm font-medium px-3 py-1.5"
            style={!canSave ? { opacity: 0.5, cursor: "not-allowed" } : {}}
          >
            Add method
          </button>
        </div>
      </div>
    </div>
  );
}

function TabHierarchyMethods(props) {
  const {
    sourceHierarchy, autoApprove, setAutoApprove, needsReview, setNeedsReview,
    methodA, setMethodA, methodB, setMethodB, methodC, setMethodC, methodD, setMethodD, methodE, setMethodE,
    blockingFloor, setBlockingFloor, keepDigits, setKeepDigits, stripParens, setStripParens,
    stripSuffixWords, setStripSuffixWords, caseSensitive, setCaseSensitive, setActiveTab,
    sourceFile, targetFile, setToast, customMethods, setCustomMethods,
  } = props;
  const [addMethodOpen, setAddMethodOpen] = useState(false);

  function addCustomMethod(m) {
    setCustomMethods([...customMethods, { id: `custom_${Date.now()}`, version: 1, enabled: true, ...m }]);
    setAddMethodOpen(false);
    setToast(`Added "${m.name}". Saved to your config package only; the backend does not run custom scripts.`);
  }
  function toggleCustomMethod(id) {
    setCustomMethods(customMethods.map((m) => (m.id === id ? { ...m, enabled: !m.enabled } : m)));
  }
  function removeCustomMethod(id, name) {
    setCustomMethods(customMethods.filter((m) => m.id !== id));
    setToast(`Removed "${name}".`);
  }

  const chain = sourceHierarchy.length ? [...sourceHierarchy, "Match column"] : ["Level 1", "Level 2", "Level 3"];
  const maxRows = Math.max(sourceFile.rowCount || 0, targetFile.rowCount || 0);
  const bothHaveGeometry = !!(sourceFile.geometry && targetFile.geometry);

  function applyRecommended() {
    setMethodA(true);
    setMethodB(true);
    setMethodC(false);
    setMethodD(bothHaveGeometry);
    setMethodE(maxRows >= 10000);
    setAutoApprove(0.95);
    setNeedsReview(0.80);
    setBlockingFloor(0.60);
    setKeepDigits(true);
    setStripParens(true);
    setCaseSensitive(false);
    setToast(
      maxRows >= 10000
        ? `Applied recommended settings, including Splink since you're linking ${maxRows.toLocaleString()} rows.`
        : "Applied recommended settings for a run this size."
    );
  }

  const alreadyRecommended =
    methodA && methodB && !methodC && methodD === bothHaveGeometry && methodE === (maxRows >= 10000) &&
    autoApprove === 0.95 && needsReview === 0.80 && blockingFloor === 0.60 &&
    keepDigits && stripParens && !caseSensitive;

  return (
    <div className="p-5 max-w-5xl mx-auto flex flex-col gap-4">
      {!alreadyRecommended && (
      <div className="card surf2 border p-3.5 flex items-center justify-between gap-3">
        <div className="text-xs muted">
          Based on your files: {maxRows > 0 ? `${maxRows.toLocaleString()} rows, ` : ""}{bothHaveGeometry ? "geometry on both sides" : "no geometry on one or both sides"}.
          {maxRows >= 10000 && " Recommended settings would turn on Splink for a run this size."}
        </div>
        <button onClick={applyRecommended} className="btn btn-primary text-xs font-medium px-3 py-1.5 flex-shrink-0">Use recommended settings</button>
      </div>
      )}

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
          <ThresholdSlider label="Auto-approve at" value={autoApprove} setValue={setAutoApprove} accent="var(--success-track)" />
          <ThresholdSlider label="Needs-review floor" value={needsReview} setValue={setNeedsReview} accent="var(--warning-track)" />
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
          <ThresholdSlider label="Hierarchy blocking floor" value={blockingFloor} setValue={setBlockingFloor} accent="var(--primary-track)" />
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
            name={METHOD_META.C.label} engine="local sentence-embedding model" accent={METHOD_META.C.accent}
            description="Semantic matching via a local sentence-transformers model, good for meaning gaps rather than spelling gaps. Needs the optional backend package (pip install -r relink-api/requirements-embedding.txt); the first run downloads the model once unless RELINK_EMBEDDING_MODEL points at a local folder."
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
          {customMethods.map((m) => (
            <MethodCard
              key={m.id}
              name={m.name} engine={`your script, v${m.version}`} accent={m.color || "var(--method-a)"}
              description={m.description || "Custom method, no description given."}
              warning="Saved in the config package only. The backend does not run custom scripts, so this has no effect on matching results."
              enabled={m.enabled} onToggle={() => toggleCustomMethod(m.id)}
              footer={
                <div className="mt-3 flex items-center justify-between gap-2">
                  <span className="text-xs muted font-mono truncate" title={m.path}>{m.path}</span>
                  <button onClick={() => removeCustomMethod(m.id, m.name)} className="text-xs flex-shrink-0" style={{ color: "var(--danger)" }}>Remove</button>
                </div>
              }
            />
          ))}
          <AddMethodCard onClick={() => setAddMethodOpen(true)} />
        </div>
      </div>
      {addMethodOpen && (
        <AddMethodModal
          onClose={() => setAddMethodOpen(false)}
          onSave={addCustomMethod}
          takenColors={["var(--method-a)", "var(--method-b)", "var(--method-c)", "var(--method-d)", "var(--method-e)", ...customMethods.map((m) => m.color)]}
        />
      )}
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

  const [selectedPreset, setSelectedPreset] = useState(null);

  const patternValid = useMemo(() => {
    if (!excludePattern.trim()) return true;
    try {
      new RegExp(excludePattern, "i");
      return true;
    } catch {
      return false;
    }
  }, [excludePattern]);

  const SAFETY_PRESETS = {
    strict: { collisionGuard: true, allowManyToOne: false, autoDowngradeTies: true, requireHierarchyMatch: true, strictZoneMatch: true, flagLowCoverageZones: true },
    balanced: { collisionGuard: true, allowManyToOne: false, autoDowngradeTies: true, requireHierarchyMatch: true, strictZoneMatch: false, flagLowCoverageZones: true },
    loose: { collisionGuard: false, allowManyToOne: true, autoDowngradeTies: false, requireHierarchyMatch: false, strictZoneMatch: false, flagLowCoverageZones: false },
  };
  function applySafetyPreset(name) {
    setSelectedPreset(name);
    const p = SAFETY_PRESETS[name];
    setCollisionGuard(p.collisionGuard);
    setAllowManyToOne(p.allowManyToOne);
    setAutoDowngradeTies(p.autoDowngradeTies);
    setRequireHierarchyMatch(p.requireHierarchyMatch);
    setStrictZoneMatch(p.strictZoneMatch);
    setFlagLowCoverageZones(p.flagLowCoverageZones);
  }

  return (
    <div className="p-5 max-w-5xl mx-auto flex flex-col gap-4">
      <div className="card surf border p-4">
        <div className="text-xs font-semibold muted uppercase tracking-wide mb-3">Quick preset</div>
        <div className="flex gap-3">
          <ShapeOption label="Strict" description="Every safeguard on. Slower to reach full coverage, fewest false positives." active={selectedPreset === "strict"} onClick={() => applySafetyPreset("strict")} diagram="most caution" />
          <ShapeOption label="Balanced" description="A sensible default: collision guard and hierarchy checks on, exact zone matching off." active={selectedPreset === "balanced"} onClick={() => applySafetyPreset("balanced")} diagram="the defaults below" />
          <ShapeOption label="Loose" description="Minimal friction, trusts the name match more than the surrounding rules. More coverage, more to double-check." active={selectedPreset === "loose"} onClick={() => applySafetyPreset("loose")} diagram="least caution" />
        </div>
        <div className="text-xs muted mt-3">Just sets the toggles below to a starting combination, feel free to fine-tune any of them afterward.</div>
      </div>

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
  unmatched: { label: "Unmatched", color: "var(--text-muted)", soft: "var(--surface-2)" },
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
      className={`card border p-3.5 flex flex-col gap-2.5 text-left transition-all ${data.name ? "cursor-pointer" : "cursor-default opacity-60"} ${isChosen ? "approvedGlow" : ""}`}
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

// How many of the enabled candidate methods found a target, and how many
// distinct targets they named. Counts every enabled method, so a method that
// found nothing counts against consensus instead of being silently ignored.
function consensusInfo(row, keys) {
  const pool = keys && keys.length ? keys : CANDIDATE_METHOD_KEYS;
  const found = pool.filter((k) => row.methods[k] && row.methods[k].targetId !== null && row.methods[k].targetId !== undefined);
  const targets = new Set(found.map((k) => row.methods[k].targetId));
  return { enabled: pool.length, found: found.length, distinct: targets.size };
}
function isFullConsensus(row, keys) {
  const i = consensusInfo(row, keys);
  return i.enabled > 0 && i.found === i.enabled && i.distinct === 1;
}

function ConsensusIndicator({ row, keys }) {
  const info = consensusInfo(row, keys);
  const pill = (bg, color, text) => <span className="text-xs font-medium px-2.5 py-1" style={{ borderRadius: 999, background: bg, color }}>{text}</span>;
  if (info.found === 0) return pill("var(--surface-2)", "var(--text-muted)", "No method matched");
  if (info.distinct > 1) return pill("var(--warning-soft)", "var(--warning)", `${info.distinct} different targets from ${info.found} of ${info.enabled} methods`);
  if (info.found === info.enabled) return pill("var(--success-soft)", "var(--success)", info.enabled === 1 ? "1 method, nothing to compare" : `All ${info.enabled} methods agree`);
  return pill("var(--warning-soft)", "var(--warning)", `${info.found} of ${info.enabled} methods agree, ${info.enabled - info.found} found nothing`);
}

const FILTERS = [
  { key: "attention", label: "Needs attention" },
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
  C: { key: "C", label: "Semantic embedding", shortLabel: "Semantic", engine: "local sentence-embedding model", accent: "var(--method-c)" },
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
/* Tab 4: Review queue                                                    */
/* Job: INVESTIGATE. One row at a time, with everything needed to make a  */
/* judgment call on it -- method-by-method comparison, spatial preview,   */
/* audit notes, manual/geocode override. This is where you pick a method  */
/* for a specific row. It never merges or bulk-decides; that's Approve &  */
/* finalize's job, one tab over -- Review queue is for the reasoning,     */
/* Approve & finalize is for the sign-off.                                */
/* ---------------------------------------------------------------------- */

function TabReviewQueue(props) {
  const {
    rows, selectedId, setSelectedId, note, setNote, setToast, sourceGeometry, sourceGeometryRows, sourceIdCol,
    patchReviewRow, geocodeLookup, pendingDecisions, merged, setActiveTab,
    bulkReview, undoReview, setPendingDecisions, needsReview, trustMethod, setTrustMethod, trustThreshold, setTrustThreshold, customMethods,
  } = props;
  const [filter, setFilter] = useState("attention");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);

  const candidateKeys = enabledMethodKeys(props, { candidatesOnly: true });
  const selected = rows.find((r) => r.sourceId === selectedId) || rows[0];

  const isDecided = (r) => r.approved || pendingDecisions[r.sourceId] === "rejected";

  // Trusted method: rows where the method you trust is confident and nobody
  // else disagrees are "clear". They leave the attention queue (you can still
  // approve them in one click) so this tab only shows the actual outliers.
  const trustActive = !!trustMethod && trustMethod !== "none" && candidateKeys.includes(trustMethod);
  const trustFloor = Math.max(trustThreshold, needsReview);
  const trustedRows = useMemo(() => {
    if (!trustActive) return [];
    return rows.filter((r) => {
      if (isDecided(r)) return false;
      if (r.kind === "collision" || r.kind === "type_leak" || r.kind === "geometry_suspect" || r.kind === "unmatched") return false;
      const t = r.methods[trustMethod];
      if (!t || t.targetId === null || t.score === null || t.score < trustFloor) return false;
      return !candidateKeys.some((k) => {
        const m = r.methods[k];
        return k !== trustMethod && m && m.targetId !== null && m.targetId !== t.targetId && m.score !== null && m.score >= needsReview;
      });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, pendingDecisions, trustActive, trustMethod, trustFloor, needsReview, candidateKeys.join(",")]);
  const trustedIdSet = useMemo(() => new Set(trustedRows.map((r) => r.sourceId)), [trustedRows]);
  const needsAttention = (r) => !isDecided(r) && !trustedIdSet.has(r.sourceId);

  const counts = useMemo(() => ({
    all: rows.length,
    attention: rows.filter(needsAttention).length,
    low_confidence: rows.filter((r) => r.kind === "low_confidence").length,
    collision: rows.filter((r) => r.kind === "collision").length,
    type_leak: rows.filter((r) => r.kind === "type_leak").length,
    geometry_suspect: rows.filter((r) => r.kind === "geometry_suspect").length,
    unmatched: rows.filter((r) => r.kind === "unmatched").length,
    approved: rows.filter((r) => r.approved).length,
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [rows, pendingDecisions, trustedIdSet]);

  const filteredRows = useMemo(() => rows.filter((r) => {
    if (filter === "attention") { if (!needsAttention(r)) return false; }
    else if (filter === "approved") { if (!r.approved) return false; }
    else if (filter !== "all" && r.kind !== filter) return false;
    if (search) {
      const q = search.toLowerCase();
      if (!r.sourceName.toLowerCase().includes(q) && !r.sourceId.includes(q)) return false;
    }
    return true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [rows, filter, search, pendingDecisions, trustedIdSet]);

  // Switching filter should never leave you looking at a row that is not in the list.
  useEffect(() => {
    if (filteredRows.length && !filteredRows.some((r) => r.sourceId === selectedId)) setSelectedId(filteredRows[0].sourceId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  const pageCount = Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pagedRows = filteredRows.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);

  useEffect(() => { setPage(0); }, [filter, search]);

  function chooseMethod(methodKey) {
    patchReviewRow(selected.sourceId, { chosen_method: methodKey, approved: true });
    setToast(`Row ${selected.sourceId}: ${METHOD_META[methodKey]?.label || methodKey} selected as final match.`);
  }

  function rejectRow() {
    patchReviewRow(selected.sourceId, { approved: false, chosen_method: null });
    setToast(`Row ${selected.sourceId} rejected. Will be written as unmatched.`);
  }

  async function approveTrusted() {
    const ids = trustedRows.map((r) => r.sourceId);
    if (ids.length === 0) return;
    const undoToken = await bulkReview("approve", ids);
    if (!undoToken) return;
    setPendingDecisions((prev) => {
      const next = { ...prev };
      ids.forEach((id) => { next[id] = "approved"; });
      return next;
    });
    const label = (METHOD_META[trustMethod] && METHOD_META[trustMethod].shortLabel) || trustMethod;
    setToast({
      message: `Approved ${ids.length} row(s) where ${label} scored ${Math.round(trustFloor * 100)}% or more and no other method disagreed.`,
      undo: () => { undoReview(undoToken); setToast("Undone."); },
    });
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
      {merged && (
        <div className="px-5 pt-4">
          <div className="card surf2 border p-3 text-xs muted flex items-center justify-between">
            <span>These decisions are already merged. Go to Approve &amp; finalize to unmerge and keep editing.</span>
            <button onClick={() => setActiveTab(4)} className="btn btn-ghost text-xs font-medium px-3 py-1">Go to Approve &amp; finalize &rarr;</button>
          </div>
        </div>
      )}
      <div className="rls-grid-review" style={{ flex: 1, minHeight: 0 }}>
        <div className="surf border-r flex flex-col">
          <div className="p-3 border-b b">
            <input type="text" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Filter rows..." className="w-full surf2 border b rounded-md text-sm px-3 py-2" style={{ color: "var(--text)" }} />
          </div>
          <div className="p-3 border-b b flex flex-col gap-2">
            <div className="text-xs font-semibold muted uppercase tracking-wide">Trusted method</div>
            <div className="flex items-center gap-2">
              <select
                value={trustActive ? trustMethod : "none"} onChange={(e) => setTrustMethod(e.target.value)}
                className="surf2 border b rounded-md text-xs px-2 py-1.5 flex-1" style={{ color: "var(--text)" }}
              >
                <option value="none">None, show every row</option>
                {candidateKeys.map((k) => <option key={k} value={k}>{METHOD_META[k].shortLabel}</option>)}
              </select>
              {trustActive && <NumberStepper value={trustThreshold} setValue={setTrustThreshold} />}
            </div>
            {trustActive && (
              <div className="text-xs muted">
                {trustedRows.length} clear row(s) hidden: {METHOD_META[trustMethod].shortLabel} scored {Math.round(trustFloor * 100)}% or more and no other method disagreed.
                {trustedRows.length > 0 && (
                  <button onClick={approveTrusted} className="btn btn-success text-xs font-medium px-3 py-1.5 mt-2 w-full">Approve these {trustedRows.length}</button>
                )}
              </div>
            )}
          </div>
          <div className="flex flex-wrap gap-1.5 p-2 border-b b">
            {FILTERS.map((f) => (
              <button key={f.key} onClick={() => setFilter(f.key)} className="btn text-xs font-medium px-2.5 py-1 flex items-center gap-1" style={filter === f.key ? { background: "var(--primary-soft)", color: "var(--primary)" } : { color: "var(--text)" }}>
                <span>{f.label}</span>
                <span className="muted">{counts[f.key]}</span>
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
            <ConsensusIndicator row={selected} keys={candidateKeys} />
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
          {customMethods && customMethods.some((m) => m.enabled) && (
            <div className="text-xs muted mt-1">Custom methods are not counted here: the backend never runs user scripts, so they produce no candidates.</div>
          )}
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

      {/* Slim status strip -- no bulk actions and no merge button here.
          Those are Approve & finalize's job; this tab only hands you off. */}
      <div className="surf border-t b flex items-center justify-between gap-4 px-5 py-2.5" style={{ flexShrink: 0 }}>
        <div className="text-xs muted">
          <span className="font-semibold" style={{ color: "var(--text)" }}>{rows.filter(isDecided).length}</span> / {rows.length} decided in this project
        </div>
        <button onClick={() => setActiveTab(4)} className="btn btn-ghost text-xs font-medium px-3 py-1.5">
          Go to Approve &amp; finalize &rarr;
        </button>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------- */
/* Tab 5: Approve & finalize                                              */
/* Job: SIGN OFF. A flat, fast checklist across every row -- not just the */
/* problem cases -- with a big Approve/Reject per row and a live "final   */
/* answer preview" that grows as you decide, so you can see exactly what  */
/* you're about to lock in before you lock it in. This is the tab's real  */
/* reason to exist alongside Review queue: Review queue lets you compare  */
/* methods on one row; this tab lets you watch the actual output dataset  */
/* take shape in real time and sign off on it. Bulk shortcuts and "Merge  */
/* and finalize" live here, not in Review queue.                          */
/* ---------------------------------------------------------------------- */

function TabApproveFinalize(props) {
  const {
    rows, setToast, patchReviewRow, bulkReview, undoReview,
    pendingDecisions, setPendingDecisions, merged, setMerged, finalRows, setActiveTab, autoApprove,
    saveEvalSet, setEvalSet,
  } = props;
  const [statusFilter, setStatusFilter] = useState("undecided");
  // A row that just got decided is held in view briefly so its green/red
  // flash is actually visible, then flagged "exiting" to play the quick
  // collapse animation, then dropped -- at which point the normal filter
  // takes over and it's simply gone. holdIds keeps it in the list past
  // the filter; exitingIds triggers the CSS collapse on that held row.
  const [holdIds, setHoldIds] = useState({});
  const [exitingIds, setExitingIds] = useState(() => new Set());
  const timersRef = useRef([]);
  useEffect(() => () => timersRef.current.forEach(clearTimeout), []);

  const candidateKeys = enabledMethodKeys(props, { candidatesOnly: true });
  const isDecided = (r) => r.approved || pendingDecisions[r.sourceId] === "rejected";
  const isRejected = (r) => !r.approved && pendingDecisions[r.sourceId] === "rejected";

  const undecidedCount = rows.filter((r) => !isDecided(r)).length;
  const decidedCount = rows.length - undecidedCount;
  const approvedRows = rows.filter((r) => r.approved);

  const statusCounts = useMemo(() => ({
    undecided: rows.filter((r) => !isDecided(r)).length,
    approved: rows.filter((r) => r.approved).length,
    rejected: rows.filter(isRejected).length,
    all: rows.length,
  }), [rows, pendingDecisions]);

  const visibleRows = useMemo(() => rows.filter((r) => {
    if (holdIds[r.sourceId]) return true;
    if (statusFilter === "undecided") return !isDecided(r);
    if (statusFilter === "approved") return r.approved;
    if (statusFilter === "rejected") return isRejected(r);
    return true;
  }), [rows, statusFilter, pendingDecisions, holdIds]);

  // Flash the row's new color, hold it in place just long enough to
  // register, then play a fast collapse -- but only when the decision
  // would actually pull it out of the current filter view. In "All",
  // or when the decision matches the filter already showing, the row
  // just stays put with its new color and nothing needs to animate out.
  function animateOut(sourceId, decision) {
    const leavesView = !(statusFilter === "all" || statusFilter === decision);
    if (!leavesView) return;
    setHoldIds((prev) => ({ ...prev, [sourceId]: true }));
    const t1 = setTimeout(() => {
      setExitingIds((prev) => new Set(prev).add(sourceId));
    }, 130);
    const t2 = setTimeout(() => {
      setHoldIds((prev) => { const next = { ...prev }; delete next[sourceId]; return next; });
      setExitingIds((prev) => { const next = new Set(prev); next.delete(sourceId); return next; });
    }, 130 + 140);
    timersRef.current.push(t1, t2);
  }

  function logEvalDecision(r, decision, best) {
    if (!saveEvalSet) return;
    setEvalSet((prev) => [
      ...prev,
      {
        source_id: r.sourceId, source_name: r.sourceName,
        target_id: best.targetId, target_name: best.name,
        decision, notes: "",
        created_at: new Date().toISOString(),
      },
    ]);
  }

  function approveRow(r) {
    const best = bestCandidate(r, candidateKeys);
    patchReviewRow(r.sourceId, { chosen_method: r.chosenMethod || best.key, approved: true });
    setPendingDecisions((prev) => ({ ...prev, [r.sourceId]: "approved" }));
    animateOut(r.sourceId, "approved");
    logEvalDecision(r, "approve", best);
  }

  function rejectRow(r) {
    patchReviewRow(r.sourceId, { approved: false, chosen_method: null });
    setPendingDecisions((prev) => ({ ...prev, [r.sourceId]: "rejected" }));
    animateOut(r.sourceId, "rejected");
    logEvalDecision(r, "reject", bestCandidate(r, candidateKeys));
  }

  async function approveAllConsensus() {
    const targets = rows.filter((r) => {
      const candidates = Object.values(r.methods).filter((m) => m.targetId !== null);
      return isFullConsensus(r, candidateKeys) && r.kind !== "collision" && !r.approved;
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

  // ---- Bulk triage: the lazy-but-reasonable path ------------------------
  // "Safe to bulk" mirrors approveAllConsensus's own definition of consensus
  // (>=2 methods agreeing on the same target, not a collision) so the two
  // don't quietly disagree with each other. Everything undecided that isn't
  // safe becomes "needs you", grouped by kind so a whole category can be
  // signed off in one tap instead of row by row.
  function isSafeToBulk(r) {
    const candidates = Object.values(r.methods).filter((m) => m.targetId !== null);
    const uniqueTargets = new Set(candidates.map((m) => m.targetId));
    return candidates.length >= 2 && uniqueTargets.size === 1 && r.kind !== "collision";
  }
  const undecidedRows = useMemo(() => rows.filter((r) => !isDecided(r)), [rows, pendingDecisions]);
  const safeRows = useMemo(() => undecidedRows.filter(isSafeToBulk), [undecidedRows]);
  const needsYouByKind = useMemo(() => {
    const groups = {};
    undecidedRows.forEach((r) => {
      if (isSafeToBulk(r)) return;
      const k = r.kind || "unmatched";
      (groups[k] = groups[k] || []).push(r);
    });
    return Object.entries(groups)
      .map(([kind, list]) => {
        const scores = list.map((r) => bestCandidate(r, candidateKeys).score).filter((s) => s !== null);
        const avg = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
        return { kind, rows: list, avg };
      })
      .sort((a, b) => b.rows.length - a.rows.length);
  }, [undecidedRows, candidateKeys]);

  // Local only -- seeded from the Hierarchy & methods setting so it starts
  // somewhere sensible, but dragging it here is a one-off for this action
  // and never writes back to that saved setting.
  const [thresholdPct, setThresholdPct] = useState(() => Math.round((autoApprove ?? 0.9) * 100));
  const thresholdMatches = useMemo(
    () => undecidedRows.filter((r) => {
      const s = bestCandidate(r, candidateKeys).score;
      return s !== null && s * 100 >= thresholdPct;
    }),
    [undecidedRows, thresholdPct, candidateKeys]
  );
  // Collapsed by default -- it's genuinely useful but tall once open, and
  // shouldn't eat the vertical space every single row list has to fit in
  // before anyone's actually asked for it.
  const [triageOpen, setTriageOpen] = useState(false);

  async function approveByIds(ids, message) {
    if (ids.length === 0) { setToast("Nothing to approve there."); return; }
    const undoToken = await bulkReview("approve", ids);
    setPendingDecisions((prev) => {
      const next = { ...prev };
      ids.forEach((id) => { next[id] = "approved"; });
      return next;
    });
    setToast({ message, undo: undoToken ? () => { undoReview(undoToken); setToast("Undone."); } : undefined });
  }
  function approveByThreshold() {
    approveByIds(thresholdMatches.map((r) => r.sourceId), `Approved ${thresholdMatches.length} row(s) scoring ${thresholdPct}% or above.`);
  }
  // Categories where approving everything is most likely to be wrong. The
  // single-category button asks for a second click, and a multi-category
  // approve asks for confirmation.
  const RISKY_KINDS = ["collision", "type_leak", "geometry_suspect", "low_confidence"];
  const KIND_WARNING = {
    collision: "These rows share a target with another row. Approving all of them links every one to that same target.",
    type_leak: "The matched target is in a different category than the one you are linking against.",
    geometry_suspect: "The matched target sits unusually far from the source record.",
    low_confidence: "The best score is below your review floor, so these are the likeliest to be wrong.",
  };
  const [confirmKind, setConfirmKind] = useState(null);
  const [selectedKinds, setSelectedKinds] = useState(() => new Set());
  function toggleKind(kind) {
    setSelectedKinds((prev) => { const next = new Set(prev); if (next.has(kind)) next.delete(kind); else next.add(kind); return next; });
  }
  function approveKind(kind, list) {
    setConfirmKind(null);
    approveByIds(list.map((r) => r.sourceId), `Approved all ${list.length} ${((KIND_META[kind] && KIND_META[kind].label) || kind).toLowerCase()} row(s).`);
  }
  async function rejectKind(kind, list) {
    const ids = list.map((r) => r.sourceId);
    const undoToken = await bulkReview("reject", ids);
    if (!undoToken) return;
    setPendingDecisions((prev) => {
      const next = { ...prev };
      ids.forEach((id) => { next[id] = "rejected"; });
      return next;
    });
    setToast({ message: `Rejected ${ids.length} ${((KIND_META[kind] && KIND_META[kind].label) || kind).toLowerCase()} row(s).`, undo: () => { undoReview(undoToken); setToast("Undone."); } });
  }
  function approveSelectedKinds() {
    const groups = needsYouByKind.filter((g) => selectedKinds.has(g.kind) && g.kind !== "unmatched");
    const ids = groups.flatMap((g) => g.rows.map((r) => r.sourceId));
    if (ids.length === 0) return;
    const risky = groups.filter((g) => RISKY_KINDS.includes(g.kind)).map((g) => ((KIND_META[g.kind] && KIND_META[g.kind].label) || g.kind).toLowerCase());
    if (risky.length && !window.confirm(`This includes ${risky.join(", ")} rows, which are the ones most likely to be wrong. Approve them anyway?`)) return;
    approveByIds(ids, `Approved ${ids.length} row(s) from ${groups.length} categor${groups.length === 1 ? "y" : "ies"}.`);
    setSelectedKinds(new Set());
  }
  function approveAllSafe() {
    approveByIds(safeRows.map((r) => r.sourceId), `Approved ${safeRows.length} row(s) with full method agreement, nothing to review there.`);
  }

  async function mergeAndFinalize() {
    if (undecidedCount > 0) {
      setToast(`${undecidedCount} row(s) still undecided. Approve, reject, or use "Approve all remaining" first.`);
      return;
    }
    setMerged(true);
    setToast("Merged. Final answer ready.");
  }

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
          <div className="flex items-center gap-2 mt-4">
            <button onClick={() => setMerged(false)} className="btn btn-ghost text-xs font-medium px-3 py-1.5">&larr; Unmerge and keep editing</button>
            <button onClick={() => setActiveTab(5)} className="btn btn-ghost text-xs font-medium px-3 py-1.5">Continue to export &amp; audit &rarr;</button>
          </div>
        </div>
      </div>
    );
  }

  if (!rows.length) {
    return (
      <div className="p-5 max-w-2xl mx-auto">
        <div className="card surf border p-6 text-center">
          <div className="text-sm font-semibold mb-1.5">Nothing to approve yet</div>
          <div className="text-sm muted">Run the pipeline and, optionally, investigate rows in Review queue first. Every row -- decided or not -- shows up here.</div>
        </div>
      </div>
    );
  }

  const STATUS_FILTERS = [
    { key: "undecided", label: "Undecided" },
    { key: "approved", label: "Approved" },
    { key: "rejected", label: "Rejected" },
    { key: "all", label: "All" },
  ];

  return (
    <div className="flex flex-col" style={{ height: "100%" }}>
      <div className="flex items-center justify-between px-5 py-2 border-b b">
        <div>
          <div className="text-sm font-semibold">Pending approvals</div>
          <div className="text-xs muted">{undecidedCount} undecided, {statusCounts.approved} approved, {statusCounts.rejected} rejected</div>
        </div>
        <div className="flex items-center gap-2">
          {STATUS_FILTERS.map((f) => (
            <button key={f.key} onClick={() => setStatusFilter(f.key)} className="btn text-xs font-medium px-2.5 py-1.5" style={statusFilter === f.key ? { background: "var(--primary-soft)", color: "var(--primary)" } : { color: "var(--text)" }}>
              {f.label} <span className="muted">{statusCounts[f.key]}</span>
            </button>
          ))}
          {undecidedCount > 0 && (
            <button onClick={approveAllRemaining} className="btn btn-primary text-xs font-medium px-3 py-1.5">Approve all remaining</button>
          )}
        </div>
      </div>

      {statusFilter === "undecided" && undecidedCount > 0 && (
        <div className="surf2 border-b b px-5 py-2.5" style={{ flexShrink: 0 }}>
          <button onClick={() => setTriageOpen(!triageOpen)} className="btn btn-ghost text-xs font-medium px-3 py-1.5 flex items-center gap-2">
            <span>Bulk actions</span>
            <span className="muted">{triageOpen ? "\u25BE" : "\u25B8"}</span>
          </button>
          {triageOpen && (
            <div className="mt-3 flex flex-col gap-3" style={{ maxHeight: "42vh", overflowY: "auto", paddingRight: 4 }}>
              <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
                <div className="card surf border p-3.5 flex flex-col gap-2.5">
                  <div>
                    <div className="text-sm font-medium">Approve by score</div>
                    <div className="text-xs muted mt-0.5">Approves every undecided row whose best score is at or above this value. Starts from your Hierarchy &amp; methods setting, changing it here does not change that setting.</div>
                  </div>
                  <div className="flex items-center gap-3">
                    <input
                      type="range" min="0" max="100" value={thresholdPct} onChange={(e) => setThresholdPct(Number(e.target.value))}
                      style={{ flex: 1, maxWidth: 260, accentColor: "var(--primary)", "--track-fill": `linear-gradient(to right, var(--primary) 0%, var(--primary) ${thresholdPct}%, var(--border) ${thresholdPct}%, var(--border) 100%)` }}
                    />
                    <div className="flex items-center gap-1 flex-shrink-0">
                      <input
                        type="number" min="0" max="100" value={thresholdPct}
                        onChange={(e) => setThresholdPct(Math.max(0, Math.min(100, Number(e.target.value) || 0)))}
                        className="surf2 border b rounded-md text-sm font-semibold text-right px-2 py-1"
                        style={{ width: "56px" }}
                      />
                      <span className="text-sm font-semibold">%</span>
                    </div>
                    <button
                      onClick={approveByThreshold}
                      disabled={thresholdMatches.length === 0}
                      className="btn btn-success text-xs font-medium px-3 py-1.5 flex-shrink-0"
                      style={thresholdMatches.length === 0 ? { opacity: 0.5, cursor: "not-allowed" } : {}}
                    >
                      Approve {thresholdMatches.length}
                    </button>
                  </div>
                </div>

                {safeRows.length > 0 && (
                  <div className="card surf border p-3.5 flex flex-col justify-between gap-2.5">
                    <div>
                      <div className="text-sm font-medium">No method disagrees: {safeRows.length} row(s)</div>
                      <div className="text-xs muted mt-0.5">At least two methods found the same target and none point somewhere else. Not a collision.</div>
                    </div>
                    <button onClick={approveAllSafe} className="btn btn-success text-xs font-medium px-3 py-1.5 self-start">Approve all {safeRows.length}</button>
                  </div>
                )}
              </div>

              {needsYouByKind.length > 0 && (
                <div>
                  <div className="flex items-center justify-between gap-3 mb-2">
                    <div className="text-xs font-semibold muted uppercase tracking-wide">Rows that need a look, by category</div>
                    <div className="text-xs muted">Approve one category, or tick several and approve them together.</div>
                  </div>
                  <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))" }}>
                    {needsYouByKind.map(({ kind, rows: list, avg }) => {
                      const risky = RISKY_KINDS.includes(kind);
                      const canApprove = kind !== "unmatched";
                      const armed = confirmKind === kind;
                      return (
                        <div key={kind} className="card surf border p-3 flex flex-col gap-2">
                          <div className="flex items-center justify-between gap-3">
                            <div className="flex items-center gap-2.5 min-w-0">
                              {canApprove && (
                                <input type="checkbox" checked={selectedKinds.has(kind)} onChange={() => toggleKind(kind)} title="Include in the combined approve below" />
                              )}
                              <KindBadge kind={kind} />
                              <span className="text-sm">{list.length} row(s){avg !== null ? `, avg ${(avg * 100).toFixed(0)}%` : ""}</span>
                            </div>
                            {canApprove ? (
                              <button
                                onClick={() => { if (risky && !armed) { setConfirmKind(kind); return; } approveKind(kind, list); }}
                                className="btn btn-success text-xs font-medium px-3 py-1.5 flex-shrink-0"
                              >
                                {armed ? `Yes, approve ${list.length}` : `Approve all ${list.length}`}
                              </button>
                            ) : (
                              <button onClick={() => rejectKind(kind, list)} className="btn btn-danger text-xs font-medium px-3 py-1.5 flex-shrink-0" title="These have no candidate target, so they cannot be approved.">Reject all {list.length}</button>
                            )}
                          </div>
                          {armed && (
                            <div className="text-xs flex items-center justify-between gap-3" style={{ color: "var(--warning)" }}>
                              <span>{KIND_WARNING[kind]}</span>
                              <button onClick={() => setConfirmKind(null)} className="muted flex-shrink-0" style={{ background: "none", border: "none", textDecoration: "underline", padding: 0 }}>Cancel</button>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                  {selectedKinds.size > 0 && (
                    <div className="flex items-center justify-between gap-3 mt-3">
                      <span className="text-xs muted">
                        {needsYouByKind.filter((g) => selectedKinds.has(g.kind)).reduce((n, g) => n + g.rows.length, 0)} row(s) in {selectedKinds.size} ticked categor{selectedKinds.size === 1 ? "y" : "ies"}
                      </span>
                      <button onClick={approveSelectedKinds} className="btn btn-success text-xs font-medium px-3 py-1.5">Approve ticked categories</button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <div style={{ flex: 1, minHeight: 0, display: "grid", gridTemplateColumns: "1fr 340px" }}>
        <div className="overflow-y-auto p-5 flex flex-col gap-3" style={{ minWidth: 0 }}>
          {visibleRows.map((r) => {
            const decided = isDecided(r);
            const rejected = isRejected(r);
            const best = bestCandidate(r, candidateKeys);
            const borderStyle = r.approved
              ? { borderColor: "var(--success-border)", background: "var(--success-soft)" }
              : rejected
              ? { borderColor: "var(--danger-border)", background: "var(--danger-soft)" }
              : { borderColor: "var(--border)" };
            const isExiting = exitingIds.has(r.sourceId);
            return (
              <div
                key={r.sourceId}
                className={"card border p-4 flex items-center justify-between gap-5" + (isExiting ? " rowExiting" : "")}
                style={{ ...borderStyle, overflow: "hidden", flexShrink: 0 }}
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2.5 mb-1">
                    <span className="text-xs muted font-mono">#{r.sourceId}</span>
                    <span className="text-base font-semibold truncate">{r.sourceName}</span>
                    <span className="muted">&rarr;</span>
                    <span className="text-sm truncate">{best.name || "no candidate"}</span>
                  </div>
                  <div className="text-xs muted flex items-center gap-1.5">
                    <KindBadge kind={r.kind} />
                    {best.score !== null ? `score ${(best.score * 100).toFixed(1)}%` : ""}
                  </div>
                </div>
                <div className="flex items-center gap-2.5 flex-shrink-0">
                  <button onClick={() => approveRow(r)} className="btn btn-success text-sm font-medium px-4 py-2">
                    {r.approved ? "Approved" : "Approve"}
                  </button>
                  <button onClick={() => rejectRow(r)} className="btn btn-danger text-sm font-medium px-4 py-2">
                    {rejected ? "Rejected" : "Reject"}
                  </button>
                </div>
              </div>
            );
          })}
          {visibleRows.length === 0 && <div className="p-4 text-sm muted">No rows in this view.</div>}
        </div>

        <div className="surf border-l overflow-y-auto p-4">
          <div className="text-sm font-semibold mb-1">Final list</div>
          <div className="text-xs muted mb-3">Updates live as you decide. Nothing is written until you merge.</div>
          <div className="card surf2 border p-3 mb-3">
            <div className="text-xs muted mb-1">Included in the final list</div>
            <div className="text-2xl font-semibold">{approvedRows.length}</div>
          </div>
          <div className="flex flex-col gap-1.5">
            {approvedRows.map((r) => (
              <div key={r.sourceId} className="flex items-center justify-between text-xs py-1 border-b b">
                <span className="truncate">{r.sourceName}</span>
                <span style={{ color: "var(--success)" }}>&#10003;</span>
              </div>
            ))}
            {approvedRows.length === 0 && <div className="text-xs muted">Nothing approved yet.</div>}
          </div>
        </div>
      </div>

      {/* Always-visible footer: bulk shortcuts + the actual merge/lock-in
          step. This is the only tab that can merge. */}
      <div className="surf border-t b flex items-center justify-between gap-4 px-5 py-3" style={{ flexShrink: 0 }}>
        <div className="flex items-center gap-4 flex-wrap">
          <div className="text-xs muted">
            <span className="font-semibold" style={{ color: "var(--text)" }}>{decidedCount}</span> / {rows.length} decided
            {undecidedCount > 0 && <span> &middot; {undecidedCount} left</span>}
          </div>
          <button onClick={approveAllConsensus} className="btn btn-success text-xs font-medium px-3 py-1.5">Approve all in full consensus</button>
          <button onClick={rejectBelowThreshold} className="btn btn-danger text-xs font-medium px-3 py-1.5">Reject below threshold score</button>
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
  const { finalRows, totalSourceRows, setToast, reviewRows, sourceGeometry, sourceGeometryRows, sourceIdCol, exportViaBackend, projectId, auditLog, fetchAuditLog, merged, setActiveTab, configName, setConfigName, configVersion, setConfigVersion, configId, onDownloadConfig, customMethods, methodA, methodB, methodC, methodD, methodE, runLog, saveEvalSet, setSaveEvalSet, evalSet, setEvalSet, pendingDecisions } = props;
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
      return isFullConsensus(r, candidateKeys);
    }).length;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finalRows, reviewRows, candidateKeys.join(",")]);

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
            decided and you've merged and finalized. Otherwise you'd be downloading a partial or undecided result.
          </div>
          <button onClick={() => setActiveTab(3)} className="btn btn-primary text-sm font-medium px-4 py-2">
            &larr; Finish review &amp; approval, then come back
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
        <div className="flex items-center justify-between mb-1">
          <div className="text-sm font-semibold">Just get the file</div>
          <button onClick={() => doExport("csv")} className="btn btn-primary text-sm font-semibold px-5 py-2.5">Download CSV</button>
        </div>
        <div className="text-xs muted">The authoritative export, source ID, source name, target ID, target name, method, and score for every approved row. If you don't want to think about format or columns, this is the one.</div>
      </div>

      <div className="card surf border p-4">
        <div className="text-xs font-semibold muted uppercase tracking-wide mb-3">Other formats (the CSV is the button above)</div>
        <div className="flex gap-3">
          {["xlsx", "geojson"].map((fmt) => (
            <button key={fmt} onClick={() => doExport(fmt)} className="btn btn-ghost border text-sm font-medium px-5 py-2.5 flex-1">Download .{fmt}</button>
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

      <div className="card surf border p-4">
        <div className="text-xs font-semibold muted uppercase tracking-wide mb-3">Configuration package</div>
        <div className="flex items-center gap-3 mb-3">
          <input
            value={configName} onChange={(e) => setConfigName(e.target.value)}
            className="surf2 border b rounded-md text-sm px-3 py-2 flex-1"
            style={{ color: "var(--text)" }} placeholder="Name this configuration, e.g. Acme_Customer_Matching"
          />
          <span className="text-xs muted surf2 border b px-2 py-1.5 rounded-md flex-shrink-0">v{configVersion}</span>
          <button onClick={() => setConfigVersion((v) => v + 1)} className="btn btn-ghost text-xs font-medium px-3 py-1.5 flex-shrink-0" title="Downloading never changes the version. Bump it yourself when the settings meaningfully change.">New version</button>
          <button onClick={onDownloadConfig} className="btn btn-ghost text-xs font-medium px-3 py-1.5 flex-shrink-0">Download</button>
        </div>
        <div className="text-xs muted">
          {configId} &middot; {customMethods.length} custom method(s) included &middot; settings only, never your actual source/target data.
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
  // Custom methods: {id, name, path, entryFunction, description, enabled, version}.
  // Frontend-only for now, see the backend note for how a path here would
  // actually get loaded, versioned, and run alongside A-E.
  const [customMethods, setCustomMethods] = useState([]);
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
  const [reviewRows, setReviewRows] = useState([]);
  const [reviewSelectedId, setReviewSelectedId] = useState(null);
  const [reviewNote, setReviewNote] = useState("");

  // Tab 5 state
  const [pendingDecisions, setPendingDecisions] = useState({});
  const [merged, setMerged] = useState(false);

  // Backend run state: which project/job we're tracking, and whether a
  // run is currently in flight (drives the "Execute pipeline" button).
  const [projectId, setProjectId] = useState(null);
  // Configuration packages: a name/version/id wrapper around the same
  // settings object already sent to the backend, so a config file is a
  // reproducible "recipe" (never the actual source/target data) that can
  // be handed to another engineer. Bumped on every download, since that's
  // the moment "what I'm sending someone" is fixed.
  const [configName, setConfigName] = useState("Untitled configuration");
  const [configVersion, setConfigVersion] = useState(1);
  const [configId, setConfigId] = useState(() => `cfg_${Math.random().toString(36).slice(2, 10)}`);
  // Run metadata: enough to answer "how was this result produced" later,
  // without a full run-history UI. runLog is local-only for now (see the
  // backend note); a real backend would persist this per project/run.
  const [runLog, setRunLog] = useState([]);
  // Ground-truth / evaluation sets: OFF by default on purpose. Only
  // populated while the toggle is on, in Approve & finalize.
  const [saveEvalSet, setSaveEvalSet] = useState(false);
  const [evalSet, setEvalSet] = useState([]);
  const [isRunning, setIsRunning] = useState(false);
  const [auditLog, setAuditLog] = useState([]);
  // Skips the full flat checklist for anyone happy trusting the defaults:
  // bulk-approves the same "safe" rows the Approve & finalize triage panel
  // would offer to bulk-approve, immediately after the pipeline finishes,
  // and drops straight onto the short needs-you list.
  const [fastMode, setFastMode] = useState(false);
  // Review queue: a method you trust, so the queue only shows real outliers.
  const [trustMethod, setTrustMethod] = useState("none");
  const [trustThreshold, setTrustThreshold] = useState(0.95);
  // File names a loaded configuration expects (shown until they are attached).
  const [expectedFiles, setExpectedFiles] = useState(null);

  /* Session persistence. Everything on screen (files, columns, settings, the  */
  /* current project, which tab you were on) is saved in this browser and put */
  /* back after a refresh. File CONTENTS are never stored in the browser: the */
  /* backend already has them, so only the file ids are kept and re-checked.  */
  const SESSION_KEY = "relink_studio_session_v1";
  const [sessionReady, setSessionReady] = useState(false);
  const skipSave = useRef(false);
  // methodStatus() reads thresholds from here so rows loaded during a restore
  // are labelled with the restored thresholds, not the defaults.
  const thresholdsRef = useRef({ autoApprove: 0.95, needsReview: 0.8 });
  thresholdsRef.current = { autoApprove, needsReview };

  const emptyFile = (label) => ({
    name: label ? `No ${label} file selected` : "No file selected", format: "-", rowCount: 0,
    columns: ["(upload a file to see columns)"], geometry: null, geometryRows: null, fileId: null, uploadError: null,
  });
  const slimFile = (f) => ({ name: f.name, format: f.format, rowCount: f.rowCount, columns: f.columns, fileId: f.fileId });
  const decisionsFromRows = (rows) => Object.fromEntries(rows.filter((r) => r.decision === "rejected").map((r) => [r.sourceId, "rejected"]));

  // Re-reads a file from the backend by id. Returns {missing: true} only when
  // the backend says it is gone. If the backend is just unreachable the saved
  // description is kept, so a refresh while it is down does not lose your setup.
  async function hydrateFile(meta, label) {
    if (!meta || !meta.fileId) return { ...emptyFile(label), ...(meta || {}), geometry: null, geometryRows: null, uploadError: null };
    try {
      const res = await apiJson(`/upload/${meta.fileId}${meta.format === "geojson" ? "?geometry=1" : ""}`);
      return {
        name: res.filename, format: res.format, rowCount: res.row_count, columns: res.columns,
        geometry: res.geometry || null, geometryRows: res.rows || null, fileId: res.file_id, uploadError: null,
      };
    } catch (e) {
      if (e && e.status === 404) return { missing: true };
      return { ...meta, geometry: null, geometryRows: null, uploadError: null };
    }
  }

  const sessionJson = JSON.stringify({
    v: 1, activeTab, projectId, merged, reviewSelectedId,
    sourceFile: slimFile(sourceFile), sourceIdCol, sourceMatchCol, sourceHierarchy,
    targetFile: slimFile(targetFile), targetIdCol, targetMatchCol, targetHierarchy,
    shape,
    chainSteps: chainSteps.map((s) => ({ id: s.id, file: slimFile(s.file), idCol: s.idCol, matchCol: s.matchCol, hierarchy: s.hierarchy, expectedFile: s.expectedFile || "" })),
    autoApprove, needsReview, methodA, methodB, methodC, methodD, methodE, customMethods,
    blockingFloor, keepDigits, stripParens, stripSuffixWords, caseSensitive,
    collisionGuard, allowManyToOne, excludePattern, typeColumn, typeExpected, strictZoneMatch,
    autoDowngradeTies, requireHierarchyMatch, flagLowCoverageZones, sortOutputBy,
    configName, configVersion, configId, fastMode, trustMethod, trustThreshold, expectedFiles,
  });

  useEffect(() => {
    try { localStorage.removeItem("relink_studio_last_project"); } catch { /* non-fatal */ }
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(SESSION_KEY) || "null"); } catch { saved = null; }
    if (!saved || saved.v !== 1) { setSessionReady(true); return undefined; }
    let cancelled = false;
    (async () => {
      try {
        [
          [setSourceIdCol, "sourceIdCol"], [setSourceMatchCol, "sourceMatchCol"], [setSourceHierarchy, "sourceHierarchy"],
          [setTargetIdCol, "targetIdCol"], [setTargetMatchCol, "targetMatchCol"], [setTargetHierarchy, "targetHierarchy"],
          [setShape, "shape"], [setAutoApprove, "autoApprove"], [setNeedsReview, "needsReview"],
          [setMethodA, "methodA"], [setMethodB, "methodB"], [setMethodC, "methodC"], [setMethodD, "methodD"], [setMethodE, "methodE"],
          [setCustomMethods, "customMethods"], [setBlockingFloor, "blockingFloor"], [setKeepDigits, "keepDigits"],
          [setStripParens, "stripParens"], [setStripSuffixWords, "stripSuffixWords"], [setCaseSensitive, "caseSensitive"],
          [setCollisionGuard, "collisionGuard"], [setAllowManyToOne, "allowManyToOne"], [setExcludePattern, "excludePattern"],
          [setTypeColumn, "typeColumn"], [setTypeExpected, "typeExpected"], [setStrictZoneMatch, "strictZoneMatch"],
          [setAutoDowngradeTies, "autoDowngradeTies"], [setRequireHierarchyMatch, "requireHierarchyMatch"],
          [setFlagLowCoverageZones, "flagLowCoverageZones"], [setSortOutputBy, "sortOutputBy"],
          [setConfigName, "configName"], [setConfigVersion, "configVersion"], [setConfigId, "configId"],
          [setFastMode, "fastMode"], [setTrustMethod, "trustMethod"], [setTrustThreshold, "trustThreshold"], [setExpectedFiles, "expectedFiles"],
        ].forEach(([set, key]) => { if (saved[key] !== undefined && saved[key] !== null) set(saved[key]); });
        thresholdsRef.current = {
          autoApprove: saved.autoApprove ?? thresholdsRef.current.autoApprove,
          needsReview: saved.needsReview ?? thresholdsRef.current.needsReview,
        };

        const lost = [];
        const [src, tgt] = await Promise.all([hydrateFile(saved.sourceFile, "source"), hydrateFile(saved.targetFile, "target")]);
        if (cancelled) return;
        if (src.missing) { lost.push(saved.sourceFile.name); setSourceFile(emptyFile("source")); setSourceIdCol(""); setSourceMatchCol(""); }
        else setSourceFile(src);
        if (tgt.missing) { lost.push(saved.targetFile.name); setTargetFile(emptyFile("target")); setTargetIdCol(""); setTargetMatchCol(""); }
        else setTargetFile(tgt);

        const steps = await Promise.all((saved.chainSteps || []).map(async (s) => {
          const f = await hydrateFile(s.file, "");
          if (f.missing) lost.push(s.file.name);
          return {
            id: s.id, file: f.missing ? emptyFile("") : f,
            idCol: f.missing ? "" : s.idCol, matchCol: f.missing ? "" : s.matchCol, hierarchy: s.hierarchy || [],
            expectedFile: s.expectedFile || (f.missing ? s.file.name : ""),
          };
        }));
        if (cancelled) return;
        setChainSteps(steps);

        let projectRestored = false;
        if (saved.projectId) {
          try {
            const rows = await fetchAllReviewRows(saved.projectId);
            if (cancelled) return;
            setProjectId(saved.projectId);
            setReviewRows(rows);
            setReviewSelectedId(rows.some((r) => r.sourceId === saved.reviewSelectedId) ? saved.reviewSelectedId : (rows[0]?.sourceId ?? null));
            setPendingDecisions(decisionsFromRows(rows));
            setMerged(!!saved.merged && rows.length > 0);
            projectRestored = true;
          } catch (e) {
            if (!(e && e.status === 404)) { setProjectId(saved.projectId); setMerged(!!saved.merged); }
            else lost.push("your last run's results");
          }
        }
        const needsProject = saved.activeTab >= 3;
        setActiveTab(needsProject && !projectRestored && !saved.projectId ? 0 : (saved.activeTab || 0));
        if (lost.length) setToast(`Restored your session, but ${lost.join(" and ")} no longer exist${lost.length === 1 ? "s" : ""} on the backend. Upload again or run the pipeline again.`);
        else if (src.fileId || tgt.fileId || projectRestored) setToast("Restored where you left off.");
      } finally {
        if (!cancelled) setSessionReady(true);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Autosave, but only after the restore above has finished, so the empty
  // defaults of a fresh page never overwrite what is saved.
  useEffect(() => {
    if (!sessionReady || skipSave.current) return undefined;
    const t = setTimeout(() => {
      try { localStorage.setItem(SESSION_KEY, sessionJson); } catch { /* private mode or quota, non-fatal */ }
    }, 150);
    return () => clearTimeout(t);
  }, [sessionJson, sessionReady]);

  function startNewProject() {
    if (!window.confirm("Start a new project? The files and settings on this screen are cleared. Nothing already saved on the backend is deleted.")) return;
    skipSave.current = true;
    try { localStorage.removeItem(SESSION_KEY); } catch { /* non-fatal */ }
    window.location.reload();
  }

  /* Backend connection: address, auth session, and live /health status -- */
  /* see ConnectionStatus in the top bar. Polled periodically so switching */
  /* the backend between --local and --lan (or a teammate's sign-in       */
  /* expiring) shows up without a page reload.                            */
  const [apiBaseState, setApiBaseState] = useState(getApiBase());
  const [authToken, setAuthTokenState] = useState(getAuthToken());
  const [authUser, setAuthUser] = useState(null);
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState(null);
  const [isCheckingHealth, setIsCheckingHealth] = useState(true);

  function refreshHealth() {
    setIsCheckingHealth(true);
    checkBackendHealth()
      .then((h) => { setHealth(h); setHealthError(null); })
      .catch((err) => { setHealth(null); setHealthError(err.message || "Could not reach the backend."); })
      .finally(() => setIsCheckingHealth(false));
  }

  useEffect(() => {
    refreshHealth();
    const id = setInterval(refreshHealth, 15000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBaseState]);

  function handleApiBaseChange(newBase) {
    const cleaned = newBase.trim().replace(/\/+$/, "");
    if (!cleaned) return;
    setApiBase(cleaned);
    setApiBaseState(cleaned);
    setToast(`Backend address updated to ${cleaned}.`);
  }

  async function handleLogin(username, password) {
    const session = await loginToBackend(username, password);
    setAuthToken(session.token);
    setAuthTokenState(session.token);
    setAuthUser(session.username);
    setToast(`Signed in as ${session.username}.`);
  }

  function handleLogout() {
    setAuthToken(null);
    setAuthTokenState(null);
    setAuthUser(null);
    setToast("Signed out.");
  }

  // The very first account has to be created from the machine running Relink,
  // and it becomes the lead. The backend refuses anything else with a clear message.
  async function handleRegister(username, password) {
    await apiJson("/auth/register", { method: "POST", body: JSON.stringify({ username, password, role: "lead" }) });
    await handleLogin(username, password);
  }

  // Asks the backend to restart itself in the other mode, then waits for it.
  async function handleSwitchMode(mode) {
    setToast(mode === "lan" ? "Turning on team sharing, restarting..." : "Switching back to this computer only, restarting...");
    try {
      await apiJson("/admin/switch-mode", { method: "POST", body: JSON.stringify({ mode }) });
    } catch (e) {
      setToast(`Could not switch: ${e.message}`);
      return;
    }
    for (let i = 0; i < 40; i += 1) {
      await new Promise((r) => setTimeout(r, 750));
      try {
        const h = await checkBackendHealth();
        if (h.mode === mode) {
          setHealth(h); setHealthError(null);
          if (mode === "local") { setAuthToken(null); setAuthTokenState(null); setAuthUser(null); }
          setToast(mode === "lan"
            ? "Team sharing is on. Open the connection panel to create the first account, then share the network link."
            : "Back to this computer only.");
          return;
        }
      } catch { /* still restarting */ }
    }
    setToast("The backend did not come back in time. Check relink.log in the project folder.");
  }

  const connection = {
    apiBase: apiBaseState, onApiBaseChange: handleApiBaseChange,
    health, healthError, isChecking: isCheckingHealth, onRefresh: refreshHealth,
    isAuthenticated: !!authToken, authUser, onLogin: handleLogin, onLogout: handleLogout,
    onSwitchMode: handleSwitchMode, onRegister: handleRegister,
  };
  const pollTimer = useRef(null);

  function methodStatus(score) {
    if (score === null || score === undefined) return null;
    const t = thresholdsRef.current;
    if (score >= t.autoApprove) return "auto_approved";
    if (score < t.needsReview) return "no_match";
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
    if (shape !== "single") {
      const incomplete = chainSteps.find((s) => !s.file.fileId || !s.idCol || !s.matchCol);
      if (chainSteps.length === 0) {
        setToast(`Add at least one ${shape === "chained" ? "chain step" : "spoke"}, or switch back to Single link.`);
        return;
      }
      if (incomplete) {
        setToast("Every step needs its own file uploaded and ID/match columns picked before running.");
        return;
      }
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

      // Run metadata: enough to answer "how was this result produced"
      // later, without a full run-history UI. Local only for now, a real
      // backend would persist this per project.
      const activeMethods = [
        methodA && METHOD_META.A.label, methodB && METHOD_META.B.label, methodC && METHOD_META.C.label,
        methodD && METHOD_META.D.label, methodE && METHOD_META.E.label,
      ].filter(Boolean);  // custom methods are deliberately excluded: the backend never ran them
      setRunLog((prev) => [
        {
          run_id: `run_${prev.length + 1}`,
          project_id: project.id,
          config_name: configName, config_version: configVersion,
          methods: activeMethods,
          row_count: rows.length,
          timestamp: new Date().toISOString(),
        },
        ...prev,
      ]);

      if (fastMode) {
        // Same "safe to bulk" definition as the Approve & finalize triage
        // panel: full method agreement, not a collision, score at or above
        // the auto-approve threshold. Uses project.id directly rather than
        // the projectId state (which hasn't re-rendered yet at this point
        // in the same synchronous flow that just called setProjectId).
        const eligible = rows.filter((r) => {
          const candidates = Object.values(r.methods).filter((m) => m.targetId !== null);
          const uniqueTargets = new Set(candidates.map((m) => m.targetId));
          const bestScore = candidates.length ? Math.max(...candidates.map((m) => m.score ?? 0)) : null;
          return candidates.length >= 2 && uniqueTargets.size === 1 && r.kind !== "collision" && bestScore !== null && bestScore >= autoApprove;
        });
        let approvedCount = 0;
        if (eligible.length > 0) {
          try {
            await apiJson(`/projects/${project.id}/review/bulk`, {
              method: "POST",
              body: JSON.stringify({ action: "approve", source_ids: eligible.map((r) => r.sourceId) }),
            });
            const approvedIds = new Set(eligible.map((r) => r.sourceId));
            setReviewRows(rows.map((r) => (approvedIds.has(r.sourceId) ? { ...r, approved: true } : r)));
            setPendingDecisions(Object.fromEntries(eligible.map((r) => [r.sourceId, "approved"])));
            approvedCount = eligible.length;
          } catch (e) {
            setToast(`Fast mode's auto-approve step failed, every row is still here to review manually: ${e.message}`);
          }
        }
        setToast({ message: `Pipeline finished: ${rows.length} row(s) matched${approvedCount ? `, ${approvedCount} auto-approved` : ""}. ${rows.length - approvedCount} left to look at.` });
        setActiveTab(4);
      } else {
        setToast({ message: `Pipeline finished: ${rows.length} row(s) matched. Opening the review queue.` });
        setActiveTab(3);
      }
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
      const res = await apiJson(`/projects/${projectId}/review/${encodeURIComponent(sourceId)}`, { method: "PATCH", body: JSON.stringify(patch) });
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
    return [...fromReview, ...fromPending].sort((a, b) => Number(a.sourceId) - Number(b.sourceId));
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
      // Each step names what it matches against (the base target for the
      // first extra step, the previous step for later ones in a chain, or
      // always the original source for a hub spoke) so the backend can
      // build the right dependency order without guessing.
      chain_steps: shape !== "single" ? chainSteps.map((s, i) => ({
        step_id: s.id,
        input_ref: shape === "chained" ? (i === 0 ? "target" : chainSteps[i - 1].id) : "source",
        file: s.file.name, file_id: s.file.fileId,
        id_column: s.idCol, match_column: s.matchCol, hierarchy: s.hierarchy,
      })) : [],
      methods: [methodA && "fuzzy", methodB && "recordlinkage", methodD && "geometry_corroboration", methodE && "splink", methodC && "embedding"].filter(Boolean),
      thresholds: { auto_approve: autoApprove, needs_review: needsReview },
      matching: { blocking_floor: blockingFloor, keep_digits: keepDigits, strip_parentheticals: stripParens, strip_suffix_words: stripSuffixWords, case_sensitive: caseSensitive },
      safety: { collision_guard: collisionGuard, auto_downgrade_ties: autoDowngradeTies, require_hierarchy_match: requireHierarchyMatch, strict_zone_match: strictZoneMatch, flag_low_coverage_zones: flagLowCoverageZones },
      output: { sort_by: sortOutputBy },
    };
  }

  function buildConfigPackage() {
    // Configuration, not data: only settings, column names, and file
    // names/paths ever go in here, never actual row contents, and never
    // credentials. custom_methods carries a path reference to the
    // engineer's own script, not the script's contents.
    return {
      relink_config: true,
      name: configName,
      version: configVersion,
      config_id: configId,
      created_at: new Date().toISOString(),
      relink_version: RELINK_VERSION,
      custom_methods: customMethods.map((m) => ({
        name: m.name, path: m.path, entry_function: m.entryFunction, description: m.description, enabled: m.enabled, version: m.version,
      })),
      settings: buildConfigObject(),
    };
  }

  function handleDownloadConfig() {
    const pkg = buildConfigPackage();
    const fileSlug = configName.trim().replace(/\s+/g, "_").replace(/[^\w-]/g, "") || "relink_studio_config";
    downloadFile(`${fileSlug}_v${configVersion}.json`, JSON.stringify(pkg, null, 2), "application/json");
    setToast(`Downloaded ${fileSlug}_v${configVersion}.json. Anyone can upload this to reproduce your exact setup (never your actual data).`);
  }

  // If the backend has a shared folder (RELINK_LIBRARY_DIR) and it holds the
  // files a configuration names, attach them so a teammate's config just works.
  async function attachFromLibrary(c) {
    let lib;
    try { lib = await apiJson("/library"); } catch { return; }
    if (!lib || !lib.enabled) return;
    const have = new Set(lib.files.map((f) => f.name));
    const pull = async (name) => {
      if (!name || !have.has(name)) return null;
      try {
        const up = await apiJson("/library/import", { method: "POST", body: JSON.stringify({ filename: name }) });
        const f = await hydrateFile({ fileId: up.file_id, format: up.format }, "");
        return f.missing ? null : f;
      } catch { return null; }
    };
    let attached = 0;
    const src = await pull(c.source && c.source.file);
    if (src) { setSourceFile(src); attached += 1; }
    const tgt = await pull(c.target && c.target.file);
    if (tgt) { setTargetFile(tgt); attached += 1; }
    const steps = c.chain_steps || [];
    for (let i = 0; i < steps.length; i += 1) {
      const f = await pull(steps[i].file);
      if (f) { attached += 1; setChainSteps((prev) => prev.map((st, idx) => (idx === i ? { ...st, file: f } : st))); }
    }
    if (attached) setToast(`Attached ${attached} file(s) from the shared folder.`);
  }

  function handleUploadConfig(e) {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const raw = JSON.parse(reader.result);
        // Backward compatible: an older, unwrapped flat config still loads
        // fine, it just arrives with no name/version/custom methods of
        // its own.
        const isPackage = raw && raw.relink_config === true;
        const c = isPackage ? raw.settings : raw;
        if (isPackage) {
          setConfigName(raw.name || "Untitled configuration");
          setConfigVersion(typeof raw.version === "number" ? raw.version : 1);
          if (Array.isArray(raw.custom_methods)) {
            setCustomMethods(raw.custom_methods.map((m, i) => ({
              id: `custom_${Date.now()}_${i}`, name: m.name, path: m.path, entryFunction: m.entry_function || "score",
              description: m.description || "", enabled: !!m.enabled, version: m.version || 1,
            })));
          }
        }
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
        if (c.chain_steps) {
          // A config file only carries column choices, not the files
          // themselves, each step comes back needing its file reattached.
          setChainSteps(c.chain_steps.map((s, i) => ({
            id: s.step_id || `step_${Date.now()}_${i}`,
            file: { name: "No file selected", format: "-", rowCount: 0, columns: ["(upload a file to see columns)"], geometry: null, geometryRows: null, fileId: null, uploadError: null },
            idCol: s.id_column || "", matchCol: s.match_column || "", hierarchy: s.hierarchy || [],
            expectedFile: s.file || "",
          })));
        }
        if (c.methods) {
          setMethodA(c.methods.includes("fuzzy"));
          setMethodB(c.methods.includes("recordlinkage"));
          setMethodD(c.methods.includes("geometry_corroboration"));
          setMethodE(c.methods.includes("splink"));
          setMethodC(c.methods.includes("embedding"));
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
        setExpectedFiles({ source: (c.source && c.source.file) || "", target: (c.target && c.target.file) || "" });
        attachFromLibrary(c);
        setToast(isPackage ? `Loaded "${raw.name}" v${raw.version}. Setup now matches that configuration.` : `Loaded configuration from ${file.name}. Setup now matches the file you uploaded.`);
      } catch (err) {
        setToast(`Could not read ${file.name}: not a valid ReLink Studio config file.`);
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
    customMethods, setCustomMethods,
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
    configName, setConfigName, configVersion, setConfigVersion, configId, onDownloadConfig: handleDownloadConfig,
    trustMethod, setTrustMethod, trustThreshold, setTrustThreshold, expectedFiles,
    runLog, saveEvalSet, setSaveEvalSet, evalSet, setEvalSet,
  };

  // What has to be true before "Continue" is allowed to move off the current tab.
  // Keeps the nav footer's enabled/disabled state consistent across every tab.
  const filesReady = sourceFile.name !== "No source file selected" && targetFile.name !== "No target file selected"
    && sourceIdCol && sourceMatchCol && targetIdCol && targetMatchCol
    && sourceFile.fileId && targetFile.fileId
    && (shape === "single" || (chainSteps.length > 0 && chainSteps.every((s) => s.file.fileId && s.idCol && s.matchCol)));
  const methodChosen = methodA || methodB || methodC || methodD || methodE;

  let navProps = { activeTab, setActiveTab };
  if (activeTab === 0) {
    navProps = {
      ...navProps,
      canContinue: filesReady,
      blockedReason: (sourceFile.uploadError || targetFile.uploadError)
        ? "Backend upload failed for one of your files, check the warning above and re-upload."
        : (shape !== "single" && chainSteps.some((s) => !s.file.fileId || !s.idCol || !s.matchCol))
        ? "Every chain step/spoke needs its own file uploaded and columns picked too."
        : "Upload both a source and target file to continue.",
    };
  } else if (activeTab === 1) {
    navProps = { ...navProps, canContinue: methodChosen, blockedReason: "Enable at least one matching method to continue." };
  } else if (activeTab === 4) {
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
      <TopBar theme={theme} setTheme={setTheme} activeTab={activeTab} setActiveTab={setActiveTab} onDownloadConfig={handleDownloadConfig} onUploadConfig={handleUploadConfig} setToast={setToast} sourceFile={sourceFile} targetFile={targetFile} onExecutePipeline={runPipeline} isRunning={isRunning} canRun={!!(sourceFile.fileId && targetFile.fileId)} merged={merged} connection={connection} fastMode={fastMode} setFastMode={setFastMode} onNewProject={startNewProject} />

      <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
        {activeTab === 0 && <TabSourcesShape {...tabProps} />}
        {activeTab === 1 && <TabHierarchyMethods {...tabProps} />}
        {activeTab === 2 && <TabSafetyRules {...tabProps} />}
        {activeTab === 3 && <TabReviewQueue {...tabProps} />}
        {activeTab === 4 && <TabApproveFinalize {...tabProps} />}
        {activeTab === 5 && <TabExportAudit {...tabProps} />}
      </div>

      <NavFooter {...navProps} />
    </div>
  );
}

