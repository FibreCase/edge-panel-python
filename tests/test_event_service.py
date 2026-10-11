from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.event_service import get_event_summary


class EventServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'calendar.ics'
        self.now = datetime(2026, 10, 9, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.env = patch.dict('os.environ', {'EVENT_TIMEZONE': 'Asia/Shanghai'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def read(self, events):
        self.path.write_text('BEGIN:VCALENDAR\nVERSION:2.0\n' + events + '\nEND:VCALENDAR\n')
        return get_event_summary(self.path, now=self.now)

    def test_orders_active_and_future_events_and_converts_utc(self):
        result = self.read('''BEGIN:VEVENT
UID:future
DTSTART:20261009T060000Z
SUMMARY:稍后
END:VEVENT
BEGIN:VEVENT
UID:active
DTSTART:20261009T030000Z
DTEND:20261009T050000Z
SUMMARY:会议
LOCATION:主楼B412
END:VEVENT''')
        self.assertEqual(result, {'name': '会议', 'time': '11:00', 'date': '2026-10-09', 'location': '主楼B412'})

    def test_recurrence_exclusion_and_cancelled_event(self):
        result = self.read('''BEGIN:VEVENT
UID:daily
DTSTART:20261001T140000
RRULE:FREQ=DAILY;COUNT=12
EXDATE:20261009T140000
SUMMARY:课程
END:VEVENT
BEGIN:VEVENT
UID:cancelled
DTSTART:20261009T130000
STATUS:CANCELLED
SUMMARY:已取消
END:VEVENT''')
        self.assertEqual(result['date'], '2026-10-10')
        self.assertEqual(result['time'], '14:00')

    def test_all_day_is_ignored(self):
        result = self.read('''BEGIN:VEVENT
UID:day
DTSTART;VALUE=DATE:20261009
SUMMARY:假期
END:VEVENT''')
        self.assertEqual(result['name'], '暂无日程')
        self.assertEqual(result['time'], 'N/A')
        self.assertEqual(result['location'], 'N/A')

    def test_recurring_all_day_does_not_hide_timed_midnight_event(self):
        result = self.read('''BEGIN:VEVENT
UID:day
DTSTART;VALUE=DATE:20261001
RRULE:FREQ=DAILY
SUMMARY:全天日程
END:VEVENT
BEGIN:VEVENT
UID:midnight
DTSTART:20261010T000000
SUMMARY:午夜会议
END:VEVENT''')
        self.assertEqual(result['name'], '午夜会议')
        self.assertEqual(result['time'], '00:00')
        self.assertEqual(result['date'], '2026-10-10')

    def test_missing_empty_and_past_calendar(self):
        self.assertEqual(get_event_summary(self.path, now=self.now)['name'], '暂无日程')
        self.assertEqual(self.read('')['date'], 'N/A')
        self.assertEqual(self.read('''BEGIN:VEVENT
UID:past
DTSTART:20261008T140000
END:VEVENT''')['date'], 'N/A')

    def test_configured_path_and_file_reload(self):
        with patch.dict('os.environ', {'EVENT_ICS_FILE': str(self.path)}):
            self.read('')
            self.assertEqual(get_event_summary(now=self.now)['name'], '暂无日程')
            self.read('''BEGIN:VEVENT
UID:new
DTSTART:20261010T140000
SUMMARY:新增
END:VEVENT''')
            self.assertEqual(get_event_summary(now=self.now)['name'], '新增')

    def test_invalid_calendar_raises(self):
        self.path.write_text('invalid')
        with self.assertRaises(ValueError):
            get_event_summary(self.path, now=self.now)


if __name__ == '__main__':
    unittest.main()
