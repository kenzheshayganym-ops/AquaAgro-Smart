"""Fail startup if authentication or actual trace delivery cannot be verified."""
from datetime import datetime, timedelta, timezone
import time
from langfuse import Langfuse, propagate_attributes


def start_tracker(settings, *, client_factory=Langfuse, wait=time.sleep):
    client = client_factory(public_key=settings.lf_public, secret_key=settings.lf_secret,
                            base_url=settings.lf_url, timeout=10,
                            environment=settings.environment)
    try:
        if not client.auth_check():
            raise RuntimeError('Langfuse authentication failed')
        trace_id = client.create_trace_id()
        with propagate_attributes(user_id='startup-check', session_id='startup',
                                  tags=['aquaagro', settings.environment, 'startup']):
            with client.start_as_current_observation(
                name='service-startup-check', as_type='span',
                trace_context={'trace_id': trace_id}, input={'check': 'startup'},
            ) as span:
                span.update(output={'status': 'ok'})
        client.flush()
        now = datetime.now(timezone.utc)
        for attempt in range(12):
            observations = client.api.observations.get_many(
                trace_id=trace_id, from_start_time=now-timedelta(hours=1),
                to_start_time=now+timedelta(minutes=5), limit=10,
            )
            if observations.data:
                return client, trace_id
            if attempt < 11:
                wait(3)
        raise RuntimeError('Langfuse accepted the export but the startup trace was not readable')
    except Exception as exc:
        try:
            client.shutdown()
        except Exception:
            pass
        # Do not echo server payloads, URLs with credentials, or SDK exceptions.
        raise RuntimeError('Langfuse startup check failed: ' + type(exc).__name__) from None
