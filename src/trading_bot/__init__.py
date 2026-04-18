__version__ = "0.1.0"

# Load .env into os.environ at package import so SSL_CERT_FILE /
# REQUESTS_CA_BUNDLE take effect before any HTTPS client is constructed.
# pydantic-settings doesn't push its loaded values back to os.environ — it
# keeps them inside the Settings object — so for env vars consumed by
# third-party libs (OpenSSL, httpx, requests) we need an explicit load.
import os as _os
from pathlib import Path as _Path

try:
    from dotenv import load_dotenv as _load

    _env_path = _Path(__file__).resolve().parent.parent.parent / ".env"
    if _env_path.exists():
        _load(_env_path)
except ImportError:
    pass
