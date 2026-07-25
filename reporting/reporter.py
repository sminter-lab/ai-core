import functools
import traceback
from datetime import datetime

from . import notion_client, config


def report_job(job_name, job_type="AI Systems"):
    """Decorator: wrap a job's entry point with self-reporting to Notion."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            token = config.NOTION_TOKEN
            source = config.MACHINE_NAME
            try:
                result = func(*args, **kwargs)
                notion_client.log_automation(
                    token,
                    job_name,
                    job_type,
                    "Success",
                    source,
                    details=str(result) if result else "Completed normally",
                )
                notion_client.upsert_heartbeat(
                    token,
                    source,
                    "OK",
                    notes=f"Last job: {job_name} @ {datetime.now().isoformat()}",
                )
                return result
            except Exception as e:
                notion_client.log_automation(
                    token,
                    job_name,
                    job_type,
                    "Failed",
                    source,
                    details=f"{e}\n{traceback.format_exc()[-500:]}",
                )
                notion_client.upsert_heartbeat(
                    token, source, "ERROR", notes=f"{job_name} failed: {e}"
                )
                raise

        return wrapper

    return decorator
