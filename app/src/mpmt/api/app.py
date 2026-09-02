from fastapi import FastAPI
from mpmt.log import setup_logging

def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title="MP-GIS_MT", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app

app = create_app()
