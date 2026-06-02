import logging

from fastapi import FastAPI

from orchestrator.routes.admin import router as admin_router
from orchestrator.routes.pipelines import router as pipelines_router
from orchestrator.routes.runs import api_router, worker_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s")


# Scaleway Serverless Containers freeze CPU between requests, so we don't
# rely on background asyncio tasks. Dispatch + reconcile run via a cron
# trigger calling /admin/tick (see routes/admin.py).
app = FastAPI(title="autometa-jobs orchestrator", version="0.1.0")
app.include_router(admin_router)
app.include_router(pipelines_router)
app.include_router(api_router)
app.include_router(worker_router)


@app.get("/")
@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
