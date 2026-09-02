from fastapi import FastAPI
from mpmt.log import setup_logging

def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title="MP-GIS_MT", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    from mpmt.api.routes_me import router as me_router
    app.include_router(me_router)
    from mpmt.api.routes_journal import router as journal_router
    app.include_router(journal_router)

    return app

app = create_app()
