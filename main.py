"""
Main entry point for SRE Agent FastAPI application.

Starts the webhook server and exposes RCA investigation endpoints.

Author: Jordan (DEV-1)
"""

import logging
import sys
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from api.webhook import router as webhook_router
from api.dashboard import router as dashboard_router
from config import settings

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOGGING CONFIGURATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FASTAPI APPLICATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

app = FastAPI(
    title="SRE Agent - Smart Root Cause Analyser",
    description=(
        "Autonomous pipeline that receives production alerts, classifies failures, "
        "investigates root causes using logs/git/jira, and generates RCA reports."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MIDDLEWARE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ROUTERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

app.include_router(webhook_router)
app.include_router(dashboard_router)

# Serve static report files (HTML, JSON, MD)
app.mount("/static", StaticFiles(directory="reports"), name="static")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ROOT ENDPOINT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.get("/dashboard-ui", tags=["dashboard"], include_in_schema=False)
async def dashboard_ui():
    """Redirect to the interactive dashboard HTML"""
    return RedirectResponse(url="/static/dashboard.html")


@app.get("/report/{report_id}", tags=["dashboard"], include_in_schema=False)
async def view_report(report_id: str):
    """Direct shareable link for a specific RCA report — opens dashboard with report in fullscreen."""
    return RedirectResponse(url=f"/static/dashboard.html?report={report_id}")


@app.get("/", tags=["root"])
async def root():
    """Root endpoint with service information"""
    return {
        "service": "SRE Agent",
        "version": "0.1.0",
        "description": "Smart Root Cause Analyser for production alerts",
        "endpoints": {
            "webhook": "/webhook/alert (POST)",
            "health": "/webhook/health (GET)",
            "dashboard_ui": "/dashboard-ui (GET)",
            "dashboard_stats": "/dashboard/stats (GET)",
            "dashboard_reports": "/dashboard/reports (GET)",
            "docs": "/docs",
        },
        "status": "operational"
    }


@app.get("/health", tags=["health"])
async def health():
    """Application health check"""
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "log_level": settings.LOG_LEVEL
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STARTUP EVENT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.on_event("startup")
async def startup_event():
    """Run on application startup"""
    logger.info("=" * 60)
    logger.info("SRE Agent - Smart Root Cause Analyser")
    logger.info("=" * 60)
    logger.info(f"App Name: {settings.APP_NAME}")
    logger.info(f"Log Level: {settings.LOG_LEVEL}")
    logger.info(f"API Host: {settings.API_HOST}:{settings.API_PORT}")
    logger.info(f"Report Output: {settings.REPORT_OUTPUT_DIR}")
    logger.info("=" * 60)
    logger.info("Pipeline: Alert → Classification → Tools → Reasoning → Report")
    logger.info("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    """Run on application shutdown"""
    logger.info("SRE Agent shutting down...")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN (for direct execution)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=True,  # Enable for development
        log_level=settings.LOG_LEVEL.lower()
    )
