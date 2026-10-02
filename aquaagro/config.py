"""Configuration comes only from the environment."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class Settings:
    openai_key: str = field(repr=False)
    lf_public: str = field(repr=False)
    lf_secret: str = field(repr=False)
    lf_url: str
    model: str
    environment: str
    db_path: Path
    data_dir: Path
    freshness: bool

    @classmethod
    def from_env(cls):
        names = ['OPENAI_API_KEY', 'LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY']
        missing = [n for n in names if not os.environ.get(n, '').strip()]
        if missing:
            raise RuntimeError('Missing required variables: ' + ', '.join(missing))
        raw = os.environ.get('ENFORCE_FRESHNESS', 'true').lower()
        if raw not in {'true', 'false'}:
            raise RuntimeError('ENFORCE_FRESHNESS must be true or false')
        url = os.environ.get('LANGFUSE_BASE_URL', 'https://cloud.langfuse.com').rstrip('/')
        parsed = urlparse(url)
        if parsed.scheme not in {'https', 'http'} or not parsed.netloc or parsed.username or parsed.password:
            raise RuntimeError('Invalid LANGFUSE_BASE_URL')
        return cls(
            os.environ[names[0]].strip(), os.environ[names[1]].strip(),
            os.environ[names[2]].strip(), url,
            os.environ.get('OPENAI_MODEL', 'gpt-4.1-mini-2025-04-14'),
            os.environ.get('APP_ENV', 'local-demo'),
            Path(os.environ.get('DB_PATH', str(ROOT / 'state/aquaagro.sqlite'))),
            Path(os.environ.get('DATA_DIR', str(ROOT / 'data'))), raw == 'true',
        )
