from datetime import date
from pathlib import Path

from nanobot.calendar.service import CalendarService


def test_calendar_service_reads_json_and_ingests_memory(tmp_path: Path) -> None:
    calendar = tmp_path / "calendar.json"
    calendar.write_text(
        """
        [
          {
            "id": "evt-1",
            "title": "RAG Agent 面试会议",
            "start": "2026-05-18T09:00:00",
            "end": "2026-05-18T10:00:00",
            "location": "Online"
          }
        ]
        """,
        encoding="utf-8",
    )
    service = CalendarService(tmp_path, local_json_files=["calendar.json"])

    events = service.list_events(start_date=date(2026, 5, 18), end_date=date(2026, 5, 18))
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
