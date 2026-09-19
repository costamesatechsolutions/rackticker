import unittest
from app.core.scheduler import Scheduler
from app.core.playlist import PlaylistEntry
from app.core.models import PriorityEvent


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.scheduler = Scheduler([PlaylistEntry("a","clock",8), PlaylistEntry("b","sports",10),
                                    PlaylistEntry("c","flight",10, mode="conditional")])
        self.eligible = {"a","b"}
        self.scheduler.tick(0,self.eligible)

    def test_rotation_and_conditional_skip(self):
        self.scheduler.tick(8,self.eligible)
        self.assertEqual(self.scheduler.current.module, "sports")
        self.scheduler.tick(10,self.eligible)
        self.assertEqual(self.scheduler.current.module, "clock")
        self.scheduler.tick(18,{"a","b","c"})
        self.assertEqual(self.scheduler.current.module, "flight")

    def test_interrupt_resumes_exact_remaining_dwell(self):
        self.scheduler.tick(3,self.eligible)
        self.scheduler.interrupt(PriorityEvent("flight",10,20))
        self.scheduler.tick(10,self.eligible)
        self.assertEqual(self.scheduler.current.module, "clock")
        self.assertEqual(self.scheduler.current.elapsed, 3)
        self.scheduler.tick(4.9,self.eligible)
        self.assertEqual(self.scheduler.current.module, "clock")
        self.scheduler.tick(.11,self.eligible)
        self.assertEqual(self.scheduler.current.module, "sports")

    def test_nested_interrupt_and_equal_priority_coalescing(self):
        self.assertTrue(self.scheduler.interrupt(PriorityEvent("sports",10,10)))
        self.scheduler.tick(2,self.eligible)
        self.assertFalse(self.scheduler.interrupt(PriorityEvent("sports",10,10)))
        self.assertTrue(self.scheduler.interrupt(PriorityEvent("flight",10,20)))
        self.scheduler.tick(10,self.eligible)
        self.assertEqual(self.scheduler.current.module, "sports")
        self.assertEqual(self.scheduler.current.elapsed, 2)
        self.scheduler.tick(8,self.eligible)
        self.assertEqual(self.scheduler.current.module, "clock")

    def test_automatic_event_waits_for_read_time_then_starts_at_the_break(self):
        self.scheduler.tick(2, self.eligible)
        self.assertTrue(self.scheduler.interrupt(PriorityEvent("flight", 10, 20), defer=True))
        self.assertEqual(self.scheduler.current.module, "clock")
        self.assertFalse(self.scheduler.interrupt(PriorityEvent("flight", 10, 20), defer=True))
        self.scheduler.tick(5.9, self.eligible)
        self.assertEqual(self.scheduler.current.module, "clock")
        self.scheduler.tick(.2, self.eligible)
        self.assertEqual((self.scheduler.current.kind, self.scheduler.current.module), ("interrupt", "flight"))
        self.scheduler.tick(10, self.eligible)
        self.assertEqual(self.scheduler.current.module, "sports")
        self.assertAlmostEqual(self.scheduler.current.elapsed, .1)

    def test_long_screen_is_interrupted_only_after_read_time(self):
        self.scheduler.tick(8, self.eligible)
        self.assertEqual(self.scheduler.current.module, "sports")
        self.scheduler.interrupt(PriorityEvent("flight", 10, 20), defer=True)
        self.scheduler.tick(7.5, self.eligible)
        self.assertEqual(self.scheduler.current.module, "sports")
        self.scheduler.tick(1, self.eligible)
        self.assertEqual(self.scheduler.current.kind, "interrupt")

    def test_pause_preview_and_resume(self):
        self.scheduler.paused = True
        self.scheduler.tick(30,self.eligible)
        self.assertEqual(self.scheduler.current.elapsed,0)
        self.scheduler.preview("message")
        self.scheduler.tick(999,self.eligible)
        self.assertEqual(self.scheduler.current.kind,"preview")
        self.scheduler.resume(self.eligible)
        self.assertEqual(self.scheduler.current.module,"clock")
        self.assertFalse(self.scheduler.paused)

    def test_all_disabled_then_reenabled(self):
        self.scheduler.tick(1,set())
        self.assertIsNone(self.scheduler.current)
        self.scheduler.tick(0,{"b"})
        self.assertEqual(self.scheduler.current.module,"sports")

    def test_config_replace_preserves_valid_cursor(self):
        self.scheduler.tick(3,self.eligible)
        self.scheduler.replace([PlaylistEntry("b","sports",10),PlaylistEntry("a","clock",12)], self.eligible)
        self.assertEqual(self.scheduler.current.elapsed,3)
        self.assertEqual(self.scheduler.current.duration,12)
        self.assertEqual(self.scheduler.index,1)
