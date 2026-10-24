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
    is true-ish. Writes also need ``SONIQ_DASHBOARD_API_KEY``; the global
    API-key middleware has already checked the key by the time this runs.
    There is no localhost exemption: a reverse proxy on the same host
    connects from loopback, so "local" can't be told apart from "public".
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

    if os.environ.get("SONIQ_DASHBOARD_API_KEY"):
        return  # Global middleware already validated the key.
    raise HTTPException(
        status_code=403,
        detail=(
            "Dashboard writes require SONIQ_DASHBOARD_API_KEY. Set it on "
            "the dashboard process and pass `X-API-Key: <key>` (or "
            "`?api_key=<key>`) on each write request."
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
        # no-cache so a browser picks up a new page after a Soniq upgrade.
        return HTMLResponse(get_dashboard_html(), headers={"Cache-Control": "no-cache"})

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
                status_code=400,
                detail=(
                    "Unable to replay dead-letter job (not found, or its job "
                    "is not in the modules the dashboard loaded - start "
                    "'soniq dashboard' with --jobs-modules or SONIQ_JOBS_MODULES)."
                ),
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

    @app.get("/api/dead-letter")
    async def api_dead_letter_jobs(limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Dead-letter jobs, newest first"""
        return await data.get_dead_letter_jobs(limit=limit, offset=offset)

    @app.get("/api/workers")
    async def api_workers() -> List[Dict[str, Any]]:
        """Registered workers and their last heartbeat"""
        return await data.get_workers()

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
    """The dashboard page, with the write flag filled in."""
    from importlib.resources import files

    # Keep the UI in sync with the server-side mutating-endpoint gate.
    can_write = "true" if _is_dashboard_write_enabled() else "false"
    html = files("soniq.dashboard").joinpath("index.html").read_text("utf-8")
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
