"""immo-bot package — loads .env once before any submodule reads os.environ."""
from pathlib import Path
from dotenv import load_dotenv

_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    load_dotenv(_env)
