from datetime import date, datetime, time, timedelta
from pathlib import Path

from copala.calendar.service import CalendarService


def test_calendar_service_reads_json_and_ingests_memory(tmp_path: Path) -> None:
    event_date = date.today() + timedelta(days=1)
    start = datetime.combine(event_date, time(hour=9))
    end = start + timedelta(hours=1)
    calendar = tmp_path / "calendar.json"
    calendar.write_text(
        f"""
        [
          {{
            "id": "evt-1",
            "title": "RAG Agent 面试会议",
            "start": "{start.isoformat()}",
            "end": "{end.isoformat()}",
            "location": "Online"
          }}
        ]
        """,
        encoding="utf-8",
    )
    service = CalendarService(tmp_path, local_json_files=["calendar.json"])

    events = service.list_events(start_date=event_date, end_date=event_date)
    count = service.ingest()

    assert len(events) == 1
    assert events[0].title == "RAG Agent 面试会议"
    assert count >= 1


def test_calendar_service_reads_ics(tmp_path: Path) -> None:
    ics = tmp_path / "demo.ics"
    ics.write_text(
        """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:demo-1
DTSTART:20260518T093000
DTEND:20260518T103000
SUMMARY:项目周会
LOCATION:Online
END:VEVENT
END:VCALENDAR
""",
        encoding="utf-8",
    )
    service = CalendarService(tmp_path, ics_files=["demo.ics"])

    events = service.list_events(start_date=date(2026, 5, 18), end_date=date(2026, 5, 18))

    assert len(events) == 1
    assert events[0].id == "demo-1"
