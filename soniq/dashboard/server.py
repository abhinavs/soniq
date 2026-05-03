"""
FastAPI web dashboard for Soniq.
"""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .app import DashboardService

if TYPE_CHECKING:
    from soniq.app import Soniq

try:
    import uvicorn
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False


_LOCALHOST_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _is_localhost_request(request: "Request") -> bool:
    """Whether the request originated on the same host as the dashboard.

    A request is treated as local when:
    - the TCP peer address is loopback, AND
    - the Host header (if present) names a loopback address.

    Both checks matter: a reverse proxy on the same host can connect from
    127.0.0.1 with an arbitrary `Host:` header, and we don't want to silently
    let a public-facing proxy bypass write protection.
    """
    client = request.client
    if client is None or client.host not in _LOCALHOST_HOSTS:
        return False
    host_header = request.headers.get("host", "")
    # Strip port if present
    host = host_header.split(":", 1)[0].lower()
    return host == "" or host in _LOCALHOST_HOSTS


def _is_dashboard_write_enabled() -> bool:
    """Whether mutating dashboard actions are enabled by env config."""
    import os

    return os.environ.get("SONIQ_DASHBOARD_WRITE_ENABLED", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _require_write_authorization(request: "Request") -> None:
    """Guard called from each write endpoint.

    Mutating endpoints are disabled unless ``SONIQ_DASHBOARD_WRITE_ENABLED``
    is true-ish. When writes are enabled, we still require either:
    1. ``SONIQ_DASHBOARD_API_KEY`` configured (the global API-key
       middleware will have already validated the key by the time this
       runs), or
    2. The request originates on localhost.
    """
    import os

    if not _is_dashboard_write_enabled():
        raise HTTPException(
            status_code=403,
            detail=(
                "Dashboard writes are disabled. Set "
                "SONIQ_DASHBOARD_WRITE_ENABLED=true to enable retry/cancel/delete."
            ),
        )

    api_key_set = bool(os.environ.get("SONIQ_DASHBOARD_API_KEY", ""))
    if api_key_set:
        return  # Global middleware already validated the key.
    if _is_localhost_request(request):
        return
    raise HTTPException(
        status_code=403,
        detail=(
            "Dashboard writes from non-local clients require "
            "SONIQ_DASHBOARD_API_KEY. Set it on the dashboard process "
            "and pass `X-API-Key: <key>` (or `?api_key=<key>`) on each "
            "write request."
        ),
    )


def create_dashboard_app(soniq_app: "Soniq") -> "FastAPI":
    """Create FastAPI dashboard application.

    Constructs a single ``DashboardService`` bound to ``soniq_app`` and
    stores it on the FastAPI app's ``state`` so per-request handlers
    reach the same instance. The lifespan ensures the underlying Soniq
    is initialized once at startup.
    """
    if not FASTAPI_AVAILABLE:
        raise ImportError(
            "Dashboard requires the dashboard extra. Install with: pip install 'soniq[dashboard]'"
        )

    data = DashboardService(soniq_app)

    @asynccontextmanager
    async def _lifespan(_app):
        await soniq_app.ensure_initialized()
        yield

    app = FastAPI(
        title="Soniq Dashboard",
        description="Real-time job monitoring and management",
        version="1.0.0",
        lifespan=_lifespan,
    )
    app.state.data = data

    # CORS: restrict origins to configured list or localhost default
    import os

    allowed_origins_env = os.environ.get("SONIQ_DASHBOARD_ALLOWED_ORIGINS", "")
    if allowed_origins_env:
        allowed_origins = [
            o.strip() for o in allowed_origins_env.split(",") if o.strip()
        ]
    else:
        allowed_origins = ["http://localhost:6161", "http://127.0.0.1:6161"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=len(allowed_origins) > 0 and "*" not in allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Optional API key authentication
    dashboard_api_key = os.environ.get("SONIQ_DASHBOARD_API_KEY", "")
    if dashboard_api_key:
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.responses import JSONResponse

        class APIKeyMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                # Allow the root page without auth for the HTML dashboard
                if request.url.path == "/":
                    return await call_next(request)
                key = request.headers.get("X-API-Key") or request.query_params.get(
                    "api_key"
                )
                if key != dashboard_api_key:
                    return JSONResponse(
                        {"detail": "Invalid or missing API key"}, status_code=401
                    )
                return await call_next(request)

        app.add_middleware(APIKeyMiddleware)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard_home():
        """Main dashboard page"""
        return get_dashboard_html()

    @app.get("/api/stats")
    async def api_job_stats() -> Dict[str, int]:
        """Get job statistics by status"""
        return await data.get_job_stats()

    @app.get("/api/jobs")
    async def api_recent_jobs(
        limit: int = 50, queue: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get recent jobs"""
        return await data.get_recent_jobs(limit=limit, queue=queue)

    @app.get("/api/queues")
    async def api_queue_stats() -> List[Dict[str, Any]]:
        """Get queue statistics"""
        return await data.get_queue_stats()

    @app.get("/api/metrics")
    async def api_job_metrics(hours: int = 24) -> Dict[str, Any]:
        """Get job processing metrics"""
        return await data.get_job_metrics(hours=hours)

    @app.post("/api/dead-letter/{dead_letter_id}/replay")
    async def api_replay_dead_letter(
        dead_letter_id: str, request: Request
    ) -> Dict[str, str]:
        """Replay a dead-letter row back into the live queue."""
        _require_write_authorization(request)
        new_job_id = await data.replay_dead_letter(dead_letter_id)
        if not new_job_id:
            raise HTTPException(
                status_code=400, detail="Unable to replay dead-letter job"
            )
        return {"message": "Dead-letter job replayed", "job_id": new_job_id}

    @app.delete("/api/jobs/{job_id}")
    async def api_delete_job(job_id: str, request: Request) -> Dict[str, str]:
        """Delete a job"""
        _require_write_authorization(request)
        success = await data.delete_job(job_id)
        if not success:
            raise HTTPException(status_code=400, detail="Unable to delete job")
        return {"message": "Job deleted"}

    @app.post("/api/jobs/{job_id}/cancel")
    async def api_cancel_job(job_id: str, request: Request) -> Dict[str, str]:
        """Cancel a queued job"""
        _require_write_authorization(request)
        success = await data.cancel_job(job_id)
        if not success:
            raise HTTPException(status_code=400, detail="Unable to cancel job")
        return {"message": "Job cancelled"}

    @app.get("/api/workers/stats")
    async def api_worker_stats() -> Dict[str, Any]:
        """Get worker statistics and health information"""
        return await data.get_worker_stats()

    @app.get("/api/jobs/timeline")
    async def api_job_timeline(hours: int = 24) -> List[Dict[str, Any]]:
        """Get job processing timeline for visualization"""
        return await data.get_job_timeline(hours=hours)

    @app.get("/api/jobs/types")
    async def api_job_types_stats() -> List[Dict[str, Any]]:
        """Get statistics grouped by job type/name"""
        return await data.get_job_types_stats()

    @app.get("/api/jobs/search")
    async def api_search_jobs(
        query: Optional[str] = None,
        status: Optional[str] = None,
        queue: Optional[str] = None,
        job_name: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Search and filter jobs with pagination"""
        return await data.search_jobs(
            query=query,
            status=status,
            queue=queue,
            job_name=job_name,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/jobs/{job_id}")
    async def api_job_details(job_id: str) -> Dict[str, Any]:
        """Get job details"""
        job = await data.get_job_details(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @app.get("/api/system/health")
    async def api_system_health() -> Dict[str, Any]:
        """Get overall system health metrics"""
        return await data.get_system_health()

    @app.get("/api/tasks/drift")
    async def api_task_registry_drift(
        window_minutes: int = 60,
    ) -> Dict[str, Any]:
        """Names with recent queued / dead-letter rows that no worker has
        registered. Surfaces deploy-skew between producer and consumer
        services that share a database. Backed by the
        soniq_task_registry observability table populated by workers."""
        return await data.get_task_registry_drift(window_minutes=window_minutes)

    # Plugin-registered dashboard panels. Each plugin registers a
    # ``PanelSpec`` via ``app.dashboard.add_panel(spec)``; here we expose
    # an index of available panels and a render endpoint per panel id.
    # Panels render lazily (the UI fetches ``/api/panels/{id}`` only
    # when the user opens that panel) so a slow plugin doesn't block
    # the rest of the dashboard.
    @app.get("/api/panels")
    async def api_list_panels() -> List[Dict[str, str]]:
        return [{"id": p.id, "title": p.title} for p in soniq_app.dashboard._panels]

    @app.get("/api/panels/{panel_id}")
    async def api_render_panel(panel_id: str) -> Dict[str, Any]:
        for panel in soniq_app.dashboard._panels:
            if panel.id == panel_id:
                content = await panel.render(soniq_app)
                return {
                    "id": panel.id,
                    "title": panel.title,
                    "content": content,
                }
        return {"error": f"Panel {panel_id!r} not registered"}

    return app


def get_dashboard_html() -> str:
    """Generate dashboard HTML"""
    # Keep the UI in sync with the server-side mutating-endpoint gate.
    can_write = "true" if _is_dashboard_write_enabled() else "false"
    html = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Soniq Dashboard</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 256 256' fill='none' stroke='%232c7a66' stroke-width='16' stroke-linecap='round' stroke-linejoin='round'><line x1='24' y1='216' x2='168' y2='216'/><path d='M88,116.51,58.65,88a8,8,0,0,1,2.2-13.3L68,72l57.53,21.17,54.84-32.75a32,32,0,0,1,41,7.32L240,91.64l-147.41,88a32,32,0,0,1-38-4.32L18.53,140a8,8,0,0,1,2.32-13.19L24,125.27,55.79,136Z'/></svg>">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #0f1220;
            --bg-soft: #161a2e;
            --panel: #1b213b;
            --panel-2: #141a30;
            --text: #eef1ff;
            --muted: #b6bdd8;
            --accent: #f4b266;
            --accent-2: #72e3a7;
            --danger: #ff6b6b;
            --warning: #ffd166;
            --info: #8db5ff;
            --shadow: rgba(0, 0, 0, 0.35);
            --border: rgba(255, 255, 255, 0.08);
        }

        [data-theme="light"] {
            --bg: #f6f4ef;
            --bg-soft: #f2efe9;
            --panel: #ffffff;
            --panel-2: #f5f2ec;
            --text: #1c1c1c;
            --muted: #5b6270;
            --accent: #ffb04a;
            --accent-2: #2c7a66;
            --danger: #e04b4b;
            --warning: #d9a441;
            --info: #2b5ea8;
            --shadow: rgba(0, 0, 0, 0.12);
            --border: rgba(0, 0, 0, 0.08);
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            font-family: 'Space Grotesk', system-ui, sans-serif;
            background: radial-gradient(1200px 800px at 20% -10%, var(--bg-soft) 0%, var(--bg) 55%) fixed;
            color: var(--text);
        }
        h1, h2, h3, .stat-number {
            font-family: 'Space Grotesk', system-ui, sans-serif;
        }
        .pill,
        .theme-toggle,
        .theme-toggle button,
        .btn,
        .status-badge,
        .refresh-info,
        .stat-label,
        .title p,
        .notice,
        .loading,
        th,
        td {
            font-family: 'IBM Plex Mono', ui-monospace, monospace;
        }
        .page {
            max-width: 1200px;
            margin: 0 auto;
            padding: 28px 20px 60px;
        }
        .topbar {
            display: flex;
            gap: 16px;
            align-items: center;
            justify-content: space-between;
            background: linear-gradient(135deg, #2d2f55 0%, #1c2542 100%);
            border: 1px solid var(--border);
            padding: 18px 22px;
            border-radius: 16px;
            box-shadow: 0 12px 30px var(--shadow);
            margin-bottom: 22px;
        }
        [data-theme="light"] .topbar {
            background: linear-gradient(135deg, #fff6e8 0%, #f2f5ff 100%);
        }
        .brand {
            display: flex;
            align-items: center;
            gap: 14px;
        }
        .brand-logo {
            width: 44px;
            height: 44px;
            flex-shrink: 0;
            color: var(--accent-2);
        }
        .title {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        .title h1 {
            font-size: 28px;
            margin: 0;
            letter-spacing: 0.5px;
        }
        .title p {
            margin: 0;
            color: var(--muted);
            font-size: 14px;
        }
        .pill {
            font-family: 'IBM Plex Mono', ui-monospace, monospace;
            font-size: 12px;
            padding: 6px 10px;
            border-radius: 999px;
            background: var(--panel-2);
            border: 1px solid var(--border);
            color: var(--muted);
        }
        .topbar-actions {
            display: flex;
            gap: 10px;
            align-items: center;
            flex-wrap: wrap;
        }
        .theme-toggle {
            display: inline-flex;
            border: 1px solid var(--border);
            background: var(--panel-2);
            border-radius: 999px;
            padding: 2px;
            gap: 4px;
            font-family: 'IBM Plex Mono', ui-monospace, monospace;
            font-size: 12px;
        }
        .theme-toggle button {
            border: none;
            background: transparent;
            color: var(--muted);
            padding: 6px 10px;
            border-radius: 999px;
            cursor: pointer;
            font-weight: 500;
            font-family: inherit;
        }
        .theme-toggle button.active {
            background: var(--accent-2);
            color: #0f131f;
        }
        [data-theme="light"] .theme-toggle button.active {
            background: #1f2a3a;
            color: #f6f4ef;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
            margin-bottom: 20px;
        }
        .stat-card {
            background: linear-gradient(160deg, #202749 0%, #141a30 100%);
            border: 1px solid var(--border);
            padding: 18px;
            border-radius: 14px;
            box-shadow: 0 10px 22px var(--shadow);
        }
        [data-theme="light"] .stat-card {
            background: linear-gradient(160deg, #ffffff 0%, #f5f2ec 100%);
        }
        .stat-number {
            font-size: 28px;
            font-weight: 700;
            margin-bottom: 6px;
        }
        .stat-label {
            color: var(--muted);
            font-size: 13px;
        }
        .section {
            background: var(--panel);
            border: 1px solid var(--border);
            padding: 18px;
            border-radius: 16px;
            margin-bottom: 18px;
            box-shadow: 0 10px 22px var(--shadow);
        }
        .section h2 {
            margin: 0 0 12px 0;
            font-size: 18px;
            color: var(--accent-2);
        }
        .table-wrap {
            overflow-x: auto;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            min-width: 720px;
        }
        th, td {
            padding: 12px 10px;
            border-bottom: 1px solid rgba(255,255,255,0.06);
            text-align: left;
            font-size: 13px;
            color: var(--text);
        }
        th {
            color: var(--muted);
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            font-size: 11px;
        }
        .status-badge {
            padding: 4px 8px;
            border-radius: 8px;
            font-size: 11px;
            font-weight: 600;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }
        .status-done { background: rgba(114, 227, 167, 0.18); color: #bdf7d9; }
        .status-queued { background: rgba(255, 209, 102, 0.18); color: #ffe2a3; }
        .status-cancelled { background: rgba(255, 107, 107, 0.12); color: #ffb0b0; }
        [data-theme="light"] .status-done { background: rgba(42, 160, 122, 0.18); color: #1b5e4b; }
        [data-theme="light"] .status-queued { background: rgba(255, 176, 74, 0.22); color: #6b4a00; }
        [data-theme="light"] .status-cancelled { background: rgba(224, 75, 75, 0.12); color: #7a1e1e; }
        .loading {
            text-align: center;
            padding: 16px;
            color: var(--muted);
            font-size: 14px;
        }
        .refresh-info {
            text-align: center;
            color: var(--muted);
            font-size: 12px;
            margin-top: 20px;
        }
        .notice {
            background: rgba(141, 181, 255, 0.16);
            border: 1px solid rgba(141, 181, 255, 0.35);
            color: #cfe0ff;
            padding: 10px 14px;
            border-radius: 12px;
            font-size: 12px;
            margin: 0 0 14px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        [data-theme="light"] .notice {
            background: #eef5ff;
            border: 1px solid #c4d6f2;
            color: #274060;
        }
        .btn {
            background: transparent;
            color: var(--text);
            border: 1px solid var(--border);
            padding: 7px 12px;
            border-radius: 10px;
            cursor: pointer;
            font-size: 12px;
            margin: 2px;
            font-weight: 600;
            transition: all 0.15s ease;
            font-family: 'IBM Plex Mono', ui-monospace, monospace;
        }
        .btn:hover {
            transform: translateY(-1px);
            border-color: rgba(114, 227, 167, 0.55);
            background: rgba(114, 227, 167, 0.12);
        }
        [data-theme="light"] .btn:hover {
            border-color: rgba(44, 122, 102, 0.45);
            background: rgba(44, 122, 102, 0.12);
        }
        .btn-ghost {
            background: transparent;
            color: var(--text);
            border: 1px solid var(--border);
        }
        .btn-primary {
            border-color: rgba(163, 152, 255, 0.45);
            color: #efe8ff;
            background: rgb(221 214 254 / 0.4);
        }
        [data-theme="light"] .btn-primary {
            border-color: rgba(124, 101, 188, 0.35);
            color: #3b2f63;
            background: rgb(221 214 254 / 0.7);
        }
        .btn:hover { opacity: 0.9; }
        .btn-danger {
            border-color: rgba(225, 104, 104, 0.4);
            color: #ffd8d8;
            background: rgba(225, 104, 104, 0.16);
        }
        .btn-danger:hover {
            border-color: rgba(225, 104, 104, 0.7);
            background: rgba(225, 104, 104, 0.26);
        }
        [data-theme="light"] .btn-danger {
            border-color: rgba(200, 80, 80, 0.35);
            color: #6a1d1d;
            background: rgba(200, 80, 80, 0.1);
        }
        [data-theme="light"] .btn-danger:hover {
            border-color: rgba(200, 80, 80, 0.6);
            background: rgba(200, 80, 80, 0.18);
        }
        @media (max-width: 820px) {
            .topbar { flex-direction: column; align-items: flex-start; }
            table { min-width: 540px; }
        }
        @media (max-width: 520px) {
            .page { padding: 18px 14px 40px; }
            .stat-number { font-size: 24px; }
        }
    </style>
</head>
<body>
    <div class="page">
        <div class="topbar">
            <div class="brand">
                <svg class="brand-logo" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16" stroke-linecap="round" stroke-linejoin="round" aria-label="Soniq logo" role="img">
                    <line x1="24" y1="216" x2="168" y2="216"/>
                    <path d="M88,116.51,58.65,88a8,8,0,0,1,2.2-13.3L68,72l57.53,21.17,54.84-32.75a32,32,0,0,1,41,7.32L240,91.64l-147.41,88a32,32,0,0,1-38-4.32L18.53,140a8,8,0,0,1,2.32-13.19L24,125.27,55.79,136Z"/>
                </svg>
                <div class="title">
                    <h1>Soniq Dashboard</h1>
                    <p>Real-time job monitoring and system signals</p>
                </div>
            </div>
            <div class="topbar-actions">
                <div class="theme-toggle" id="theme-toggle" role="group" aria-label="Theme toggle">
                    <button type="button" data-theme="light">Light</button>
                    <button type="button" data-theme="dark">Dark</button>
                </div>
                <button class="btn btn-ghost" id="refresh-btn" type="button">Refresh</button>
                <div class="pill">Auto-refresh: 30s</div>
            </div>
        </div>

        <div id="mode-notice"></div>
        <div class="stats-grid" id="stats-grid">
            <div class="loading">Loading statistics...</div>
        </div>

        <div class="section">
            <h2>Recent Jobs</h2>
            <div class="table-wrap" id="jobs-table">
                <div class="loading">Loading jobs...</div>
            </div>
        </div>

        <div class="section">
            <h2>Queue Statistics</h2>
            <div class="table-wrap" id="queue-stats">
                <div class="loading">Loading queue stats...</div>
            </div>
        </div>

        <div class="section">
            <h2>Performance Metrics (24h)</h2>
            <div id="metrics">
                <div class="loading">Loading metrics...</div>
            </div>
        </div>

        <div class="refresh-info">
            Dashboard auto-refreshes every 30 seconds
            <span id="last-updated"></span>
        </div>
    </div>

    <script>
        const CAN_WRITE = __SONIQ_CAN_WRITE__;
        const THEME_KEY = 'soniq_theme';
        const prefersDark = window.matchMedia('(prefers-color-scheme: dark)');

        function applyTheme(theme, source) {
            const resolved = theme || (prefersDark.matches ? 'dark' : 'light');
            document.documentElement.setAttribute('data-theme', resolved);
            const toggle = document.getElementById('theme-toggle');
            if (!toggle) return;
            const buttons = toggle.querySelectorAll('button[data-theme]');
            buttons.forEach((btn) => {
                const isActive = btn.dataset.theme === resolved;
                btn.classList.toggle('active', isActive);
                btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
            });
            toggle.setAttribute('data-source', source || 'system');
        }

        function getStoredTheme() {
            return localStorage.getItem(THEME_KEY);
        }

        window.addEventListener('DOMContentLoaded', () => {
            const storedTheme = getStoredTheme();
            applyTheme(storedTheme, storedTheme ? 'user' : 'system');
            const toggle = document.getElementById('theme-toggle');
            if (toggle) {
                toggle.addEventListener('click', (event) => {
                    const target = event.target;
                    if (!target || !target.dataset || !target.dataset.theme) return;
                    const nextTheme = target.dataset.theme;
                    localStorage.setItem(THEME_KEY, nextTheme);
                    applyTheme(nextTheme, 'user');
                });
            }
            const refreshBtn = document.getElementById('refresh-btn');
            if (refreshBtn) {
                refreshBtn.addEventListener('click', () => updateAll());
            }
            prefersDark.addEventListener('change', () => {
                if (!getStoredTheme()) applyTheme(null, 'system');
            });
        });


        async function fetchData(url) {
            try {
                const response = await fetch(url);
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                return await response.json();
            } catch (error) {
                console.error('Error fetching data:', error);
                return null;
            }
        }

        function formatDate(dateStr) {
            if (!dateStr) return '-';
            const date = new Date(dateStr);
            return date.toLocaleString();
        }

        function formatDuration(ms) {
            if (!ms) return '-';
            if (ms < 1000) return `${ms.toFixed(0)}ms`;
            if (ms < 60000) return `${(ms/1000).toFixed(1)}s`;
            return `${(ms/60000).toFixed(1)}m`;
        }

        function setFallback(targetId, message) {
            const target = document.getElementById(targetId);
            if (!target) return;
            if (target.querySelector('.loading')) {
                target.innerHTML = `<div class="loading">${message}</div>`;
            }
        }

        async function updateStats() {
            const stats = await fetchData('/api/stats');
            if (!stats) {
                setFallback('stats-grid', 'Stats unavailable. Check database connectivity.');
                return;
            }

            const html = `
                <div class="stat-card">
                    <div class="stat-number">${stats.total}</div>
                    <div class="stat-label">Total Jobs</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">${stats.queued}</div>
                    <div class="stat-label">Queued</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">${stats.done}</div>
                    <div class="stat-label">Done</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">${stats.processing}</div>
                    <div class="stat-label">Processing</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">${stats.dead_letter}</div>
                    <div class="stat-label">Dead Letter</div>
                </div>
            `;

            document.getElementById('stats-grid').innerHTML = html;
        }

        function updateModeNotice() {
            const notice = document.getElementById('mode-notice');
            if (!notice) return;
            if (!CAN_WRITE) {
                notice.innerHTML = '<div class="notice">Read-only mode. Set SONIQ_DASHBOARD_WRITE_ENABLED=true to enable actions.</div>';
            } else {
                notice.innerHTML = '';
            }
        }

        async function updateJobs() {
            const jobs = await fetchData('/api/jobs');
            if (!jobs) {
                setFallback('jobs-table', 'Jobs unavailable. Check database connectivity.');
                return;
            }

            const actionsEnabled = CAN_WRITE === true;
            let html = `
                <table>
                    <thead>
                        <tr>
                            <th>Job</th>
                            <th>Status</th>
                            <th>Queue</th>
                            <th>Attempts</th>
                            <th>Created</th>
                            ${actionsEnabled ? '<th>Actions</th>' : ''}
                        </tr>
                    </thead>
                    <tbody>
            `;

            jobs.forEach(job => {
                html += `
                    <tr>
                        <td>${job.job_name.split('.').pop()}</td>
                        <td><span class="status-badge status-${job.status}">${job.status}</span></td>
                        <td>${job.queue}</td>
                        <td>${job.attempts}/${job.max_attempts}</td>
                        <td>${formatDate(job.created_at)}</td>
                        ${actionsEnabled ? `
                        <td>
                            <button class="btn btn-danger" onclick="deleteJob('${job.id}')">Delete</button>
                        </td>` : ''}
                    </tr>
                `;
            });

            html += '</tbody></table>';
            document.getElementById('jobs-table').innerHTML = html;
        }

        async function updateQueueStats() {
            const queues = await fetchData('/api/queues');
            if (!queues) {
                setFallback('queue-stats', 'Unable to load queue stats. Ensure the database is reachable and workers are running.');
                return;
            }

            if (!Array.isArray(queues) || queues.length === 0) {
                document.getElementById('queue-stats').innerHTML = '<div class="loading">No queues yet</div>';
                return;
            }

            let html = `
                <table>
                    <thead>
                        <tr>
                            <th>Queue</th>
                            <th>Total</th>
                            <th>Queued</th>
                            <th>Processing</th>
                            <th>Done</th>
                            <th>Cancelled</th>
                            <th>Dead Letter</th>
                            <th>Avg Processing</th>
                        </tr>
                    </thead>
                    <tbody>
            `;

            queues.forEach(queue => {
                html += `
                    <tr>
                        <td>${queue.queue}</td>
                        <td>${queue.total_jobs}</td>
                        <td>${queue.queued}</td>
                        <td>${queue.processing}</td>
                        <td>${queue.done}</td>
                        <td>${queue.cancelled}</td>
                        <td>${queue.dead_letter}</td>
                        <td>${formatDuration(queue.avg_processing_time_ms)}</td>
                    </tr>
                `;
            });

            html += '</tbody></table>';
            document.getElementById('queue-stats').innerHTML = html;
        }

        async function updateMetrics() {
            const metrics = await fetchData('/api/metrics');
            if (!metrics) {
                setFallback('metrics', 'Metrics unavailable. Check database connectivity.');
                return;
            }

            const html = `
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="stat-number">${metrics.total_processed}</div>
                        <div class="stat-label">Processed</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number">${metrics.successful}</div>
                        <div class="stat-label">Successful</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number">${metrics.dead_lettered}</div>
                        <div class="stat-label">Dead Lettered</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number">${metrics.success_rate}%</div>
                        <div class="stat-label">Success Rate</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number">${formatDuration(metrics.avg_processing_time_ms)}</div>
                        <div class="stat-label">Avg Time</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number">${metrics.jobs_per_hour}</div>
                        <div class="stat-label">Jobs/Hour</div>
                    </div>
                </div>
            `;

            document.getElementById('metrics').innerHTML = html;
        }

        async function deleteJob(jobId) {
            if (!confirm('Delete this job?')) return;
            try {
                const response = await fetch(`/api/jobs/${jobId}`, { method: 'DELETE' });
                if (response.ok) {
                    await updateAll();
                }
            } catch (error) {
                console.error('Error deleting job:', error);
            }
        }

        async function updateAll() {
            updateModeNotice();
            await Promise.allSettled([
                updateStats(),
                updateJobs(),
                updateQueueStats(),
                updateMetrics()
            ]);
            const lastUpdated = document.getElementById('last-updated');
            if (lastUpdated) {
                const now = new Date();
                lastUpdated.textContent = ` · Last updated at ${now.toLocaleTimeString()}`;
            }
        }

        // Initial load
        updateAll();

        // Auto-refresh every 30 seconds
        setInterval(updateAll, 30000);
    </script>
</body>
</html>
"""
    return html.replace("__SONIQ_CAN_WRITE__", can_write)


async def run_dashboard(
    soniq_app: "Soniq",
    *,
    host: str = "127.0.0.1",
    port: int = 6161,
):
    """Run the dashboard server"""
    if not FASTAPI_AVAILABLE:
        raise ImportError(
            "Dashboard requires the dashboard extra. Install with: pip install 'soniq[dashboard]'"
        )

    app = create_dashboard_app(soniq_app=soniq_app)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)

    print(f"Soniq Dashboard starting at http://{host}:{port}")
    print("Press Ctrl+C to stop")

    await server.serve()
