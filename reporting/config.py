import os

try:  # pragma: no cover
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

MACHINE_NAME = os.environ.get("AI_CORE_MACHINE_NAME", os.uname().nodename)
NOTION_TOKEN = os.environ.get("NOTION_API_TOKEN") or os.environ.get("NOTION_API_KEY")

if not NOTION_TOKEN:
    raise RuntimeError(
        "Neither NOTION_API_TOKEN nor NOTION_API_KEY is set. Export one in this "
        "machine's environment before running any job that uses the reporting layer."
    )
