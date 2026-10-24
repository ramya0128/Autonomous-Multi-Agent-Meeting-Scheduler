"""Normalize stored meeting datetimes without changing meeting IDs."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "agents"))

from company_tools import migrate_meeting_times


if __name__ == "__main__":
    migrate_meeting_times()
