import unittest
from app.core.scheduler import GONE_FINISH, GONE_GRACE, MIN_READ_SECONDS, Scheduler
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
        scheduler = Scheduler([PlaylistEntry("s", "sports", 60)])
        scheduler.tick(0, {"s"})
        scheduler.interrupt(PriorityEvent("flight", 10, 20), defer=True)
        scheduler.tick(MIN_READ_SECONDS - .5, {"s"})
        self.assertEqual(scheduler.current.module, "sports")
        scheduler.tick(1, {"s"})
        self.assertEqual(scheduler.current.kind, "interrupt")

    def test_automatic_event_waits_past_read_time_for_mid_story_to_finish(self):
        """Past the minimum read time, an automatic event (a plane passing) must still
        wait for a crawling headline to finish its lap, not just for the 12s minimum."""
        scheduler = Scheduler([PlaylistEntry("a", "news", 60), PlaylistEntry("b", "sports", 60)])
        still_reading = [True]
        hold = lambda cursor: still_reading[0]
        scheduler.tick(0, {"a", "b"}, hold)
        scheduler.tick(MIN_READ_SECONDS + 5, {"a", "b"}, hold)   # well past the minimum read time
        scheduler.interrupt(PriorityEvent("flight", 10, 20), defer=True)
        scheduler.tick(5, {"a", "b"}, hold)
        self.assertEqual(scheduler.current.module, "news")      # still mid-headline: not cut off
        still_reading[0] = False                                 # the headline finishes its lap
        scheduler.tick(.1, {"a", "b"}, hold)
        self.assertEqual(scheduler.current.kind, "interrupt")

    def test_a_screen_that_blinks_out_is_not_snatched_from_the_reader(self):
        """A feed that hiccups or a plugin that restarts must not cut the screen on show."""
        scheduler = Scheduler([PlaylistEntry("a", "clock", 60), PlaylistEntry("b", "sports", 60)])
        scheduler.tick(0, {"a", "b"})
        scheduler.tick(GONE_GRACE - .5, set())
        self.assertEqual(scheduler.current.module, "clock")
        scheduler.tick(.1, {"a", "b"})                     # it came back
        scheduler.tick(GONE_GRACE - .5, set())             # and blinked again: the count starts over
        self.assertEqual(scheduler.current.module, "clock")
        scheduler.tick(1, {"b"})                           # gone for good
        self.assertEqual(scheduler.current.module, "sports")
    def test_a_screen_with_nothing_new_still_finishes_what_it_is_showing(self):
        """A plane leaves range halfway through its card: the card is finished, but not for ever."""
        scheduler = Scheduler([PlaylistEntry("a", "flight", 60), PlaylistEntry("b", "sports", 60)])
        scheduler.tick(0, {"a", "b"})
        midway = lambda cursor: True
        scheduler.tick(GONE_GRACE + 1, {"b"}, midway)
        self.assertEqual(scheduler.current.module, "flight")
        scheduler.tick(GONE_FINISH, {"b"}, midway)
        self.assertEqual(scheduler.current.module, "sports")
        scheduler = Scheduler([PlaylistEntry("a", "flight", 60), PlaylistEntry("b", "sports", 60)])
        scheduler.tick(0, {"a", "b"})
        scheduler.tick(GONE_GRACE + 1, {"b"}, lambda cursor: False)     # nothing left to finish
        self.assertEqual(scheduler.current.module, "sports")

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
        self.scheduler.tick(GONE_GRACE,set())
        self.assertIsNone(self.scheduler.current)
        self.scheduler.tick(0,{"b"})
        self.assertEqual(self.scheduler.current.module,"sports")

    def test_config_replace_preserves_valid_cursor(self):
        self.scheduler.tick(3,self.eligible)
        self.scheduler.replace([PlaylistEntry("b","sports",10),PlaylistEntry("a","clock",12)], self.eligible)
        self.assertEqual(self.scheduler.current.elapsed,3)
        self.assertEqual(self.scheduler.current.duration,12)
        self.assertEqual(self.scheduler.index,1)
