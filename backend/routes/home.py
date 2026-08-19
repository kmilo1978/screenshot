import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

FRONTEND_DIST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend_dist")


@router.get("/")
async def get_status():
    # If frontend is bundled (Docker/production), don't show this message —
    # the frontend catch-all in main.py will serve index.html instead.
    if os.path.isdir(FRONTEND_DIST):
        from starlette.responses import FileResponse
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))

    return HTMLResponse(
        content="<h3>Your backend is running correctly. Please open the front-end URL (default is http://localhost:5173) to use screenshot-to-code.</h3>"
    )
