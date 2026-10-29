"""Real subprocess cancellation and error propagation; no GIS or models."""
import os
from pathlib import Path
import sys
from threading import Event, Timer
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from plugin.package_process import run_package_process


class PackageProcessTests(unittest.TestCase):
    def test_long_operation_survives_poll_intervals(self):
        script="import json,sys,time; value=json.load(sys.stdin); time.sleep(.65); print(json.dumps(value))"
        self.assertEqual(run_package_process([sys.executable,'-c',script],{'路径':'中文'},os.environ.copy()),{'路径':'中文'})

    def test_cancel_before_start(self):
        event=Event();event.set()
        with self.assertRaisesRegex(ValueError,'已取消'):
            run_package_process(['missing-executable'],{},os.environ.copy(),event)

    def test_cancel_running_child_does_not_wait_for_completion(self):
        event=Event();timer=Timer(.5,event.set);timer.start()
        start=time.monotonic()
        try:
            with self.assertRaisesRegex(ValueError,'已取消'):
                run_package_process([sys.executable,'-c','import time; time.sleep(30)'],{},os.environ.copy(),event)
        finally:
            timer.cancel()
        self.assertLess(time.monotonic()-start,5)

    def test_backend_error_remains_failure(self):
        with self.assertRaisesRegex(ValueError,'broken.tif'):
            run_package_process([sys.executable,'-c',"import sys; sys.stderr.write('broken.tif'); sys.exit(2)"],{},os.environ.copy())


if __name__=='__main__':unittest.main()
