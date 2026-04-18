from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.web import BASE_DIR
from app.auth import NotAuthenticated
from app.auth.routes import router as auth_router
from app.settings.routes import router as settings_router
from app.dashboard.routes import router as dashboard_router
from app.trading.routes import router as trading_router

app = FastAPI()


@app.exception_handler(NotAuthenticated)
def not_authenticated_handler(request: Request, exc: NotAuthenticated):
    return RedirectResponse(url="/", status_code=302)


app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.include_router(auth_router)
app.include_router(settings_router)
app.include_router(dashboard_router)
app.include_router(trading_router)
