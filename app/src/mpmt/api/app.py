from fastapi import FastAPI
from fastapi.responses import JSONResponse
from mpmt.log import setup_logging

def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title="MP-GIS_MT", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz():
        # публичный, без require_scope: живой пинг БД + маркер последнего поллинга WB
        from sqlalchemy import text
        from mpmt.db import engine, SessionLocal
        from mpmt.platform.models import PlatformKV
        try:
            with engine.connect() as c:
                c.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "degraded", "db": "down"}, status_code=503)
        last_poll = None
        try:
            with SessionLocal() as s:
                kv = s.get(PlatformKV, "wb_last_poll")
                if kv is not None:
                    last_poll = kv.value
        except Exception:
            last_poll = None
        return {"status": "ok", "last_poll": last_poll}

    from mpmt.api.routes_me import router as me_router
    app.include_router(me_router)
    from mpmt.api.routes_journal import router as journal_router
    app.include_router(journal_router)

    return app

app = create_app()
