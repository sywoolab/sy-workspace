import copy
import json
import sys
import unittest
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import training_snapshot as snapshot
import garmin_sync
import workout_alert
import workout_analysis
import adaptive_scheduler


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.log, self.schedule = snapshot.load_snapshot()
        self.today = date(2026, 10, 6)
        now = datetime(2026, 10, 6, 10, tzinfo=timezone(timedelta(hours=9)))
        for module in (garmin_sync, workout_alert, workout_analysis, adaptive_scheduler):
            for field, value in [('NOW', now), ('TODAY', self.today.isoformat())]:
                patcher = patch.object(module, field, value)
                patcher.start()
                self.addCleanup(patcher.stop)

    def test_confirmed_program_and_actual_are_sent_without_legacy_claims(self):
        for formatter in (
            lambda: snapshot.format_snapshot(self.log, self.schedule, self.today),
            lambda: garmin_sync.format_workout_message([{'date': '2026-10-06'}], {}, [], self.schedule, self.log),
            workout_alert.format_morning,
        ):
            msg = formatter()
            for text in ('1125m', '22~24km', '38~42km', '30~32km', '소염제', '수영 개인강습'):
                self.assertIn(text, msg)
            for text in ('예상 2:57', '브릭 0회', 'OFF TRACK', '러닝 0/4', '최소:'):
                self.assertNotIn(text, msg)
        for i in range(5, 19):
            entry = self.schedule['overrides'][f'2026-10-{i:02}']
            self.assertIn(entry['detail'], snapshot.format_plan(self.log, self.schedule, self.today))

    def test_newly_confirmed_schedule_is_read_without_restart(self):
        changed = copy.deepcopy(self.schedule['overrides'])
        changed['2026-10-07'] = {'workout': '확정 휴식', 'detail': '새 조건'}
        with patch.object(workout_alert, 'load_schedule_overrides', return_value=changed):
            self.assertEqual(workout_alert.get_schedule_for_date(date(2026, 10, 7)), ('확정 휴식', '새 조건'))

    def test_missing_day_does_not_reintroduce_old_phase_plan(self):
        self.assertEqual(snapshot.day_plan({}, date(2026, 10, 8)), ('계획 미등록', ''))

    def test_multisession_distance_and_frequency(self):
        log = {'2026-10-06': {'done': True, 'all_metrics': [
            {'type': 'run', 'distance_km': 4}, {'type': 'run', 'distance_m': 2000},
            {'type': 'swim', 'distance_m': 1000}]}}
        self.assertEqual(snapshot.run_stats(log, self.today, self.today), (1, 6, 4))

    def test_long_messages_preserve_every_character_and_api_failure(self):
        text = ('🏊 상세 조건\n' * 2000)
        parts = list(snapshot.message_chunks(text))
        self.assertGreater(len(parts), 1)
        self.assertEqual(''.join(parts), text)
        self.assertTrue(all(len(p.encode('utf-16-le')) // 2 <= 3500 for p in parts))
        with patch('requests.post') as post:
            post.return_value.json.return_value = {'ok': True}
            self.assertTrue(snapshot.send_messages(text, 'test', 'test'))
            self.assertEqual(post.call_count, len(parts))
            post.return_value.json.return_value = {'ok': False}
            self.assertFalse(snapshot.send_messages(text, 'test', 'test'))

    def test_may_vdot_history_does_not_generate_current_plateau_alarm(self):
        self.assertIsNone(adaptive_scheduler.rule_c2_vdot_stagnation(self.schedule))
        items = adaptive_scheduler._detect_improvement_items(self.log, self.schedule, {})
        self.assertNotIn('plateau', [item['type'] for item in items])

    def test_current_analysis_preserves_confirmed_schedule(self):
        before = Path(workout_analysis.SCHEDULE_FILE).read_bytes()
        with patch.object(workout_analysis, 'format_analysis_message', side_effect=AssertionError('legacy analysis called')):
            with patch.object(sys, 'argv', ['workout_analysis.py', '--dry-run']):
                workout_analysis.main()
        self.assertEqual(Path(workout_analysis.SCHEDULE_FILE).read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
