from pathlib import Path

from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent  # корень проекта
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
