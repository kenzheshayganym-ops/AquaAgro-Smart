"""Local demonstration API. No autonomous equipment control."""
from contextlib import asynccontextmanager
import asyncio
import logging
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator
from .config import Settings
from .store import Store
from .tracing import start_tracker
from .agent import AgentService

class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    user_id: str = Field(default='demo-operator', min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_.-]+$')
    session_id: str = Field(default='demo-session', min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_.-]+$')

    @field_validator('question')
    @classmethod
    def question_not_blank(cls, value):
        if not value.strip():
            raise ValueError('Question must not be blank')
        return value.strip()


def create_app(*, runtime_factory=None):
    @asynccontextmanager
    async def lifespan(app):
        if runtime_factory is not None:
            service, startup_trace = runtime_factory()
        else:
            settings = Settings.from_env()
            store = Store(settings.db_path, settings.data_dir)
            tracker, startup_trace = await asyncio.to_thread(start_tracker, settings)
            try:
                service = AgentService(settings, store, tracker)
            except Exception:
                tracker.shutdown()
                raise
        app.state.service = service
        app.state.startup_trace = startup_trace
        logging.getLogger('aquaagro').info('Startup trace confirmed: %s', startup_trace)
        try:
            yield
        finally:
            await asyncio.to_thread(service.tracker.shutdown)

    app = FastAPI(title='AquaAgro Smart', version='1.0', lifespan=lifespan,
                  description='Учебный прототип на искусственных данных. Решение о поливе принимает человек.')

    @app.get('/', include_in_schema=False)
    def index():
        return RedirectResponse('/docs')

    @app.get('/health')
    def health():
        service = app.state.service
        try:
            service.store.check_database()
        except Exception:
            raise HTTPException(503, 'Database integrity check failed') from None
        return {'status':'ok', 'synthetic_data':True, 'model':service.settings.model,
                'freshness_guard':service.settings.freshness,
                'data_hash':service.store.data_hash, 'tracing_startup_verified':True,
                'startup_trace_id':app.state.startup_trace,
                'environment':service.settings.environment}

    @app.post('/ask')
    def ask(request: AskRequest):
        try:
            return app.state.service.ask(request.question, request.user_id, request.session_id)
        except BlockingIOError:
            raise HTTPException(429, 'Agent is busy. Retry after the current request finishes.') from None
        except RuntimeError:
            raise HTTPException(502, 'Agent request failed. Check the trace and request log.') from None

    return app

app = create_app()
