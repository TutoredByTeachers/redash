import os
import signal

from mock import MagicMock, Mock, call, patch
from rq import Connection
from rq.job import JobStatus

from redash import rq_redis_connection
from redash.tasks import Queue, Worker
from redash.tasks.queries.execution import enqueue_query
from redash.tasks.worker import HardLimitingWorker
from redash.worker import default_queues, job
from tests import BaseTestCase


@patch("statsd.StatsClient.incr")
class TestWorkerMetrics(BaseTestCase):
    def tearDown(self):
        with Connection(rq_redis_connection):
            for queue_name in default_queues:
                Queue(queue_name).empty()

    def test_worker_records_success_metrics(self, incr):
        query = self.factory.create_query()

        with Connection(rq_redis_connection):
            enqueue_query(
                query.query_text,
                query.data_source,
                query.user_id,
                False,
                None,
                {"Username": "Patrick", "query_id": query.id},
            )

            Worker(["queries"]).work(max_jobs=1)

        calls = [
            call("rq.jobs.running.queries"),
            call("rq.jobs.started.queries"),
            call("rq.jobs.running.queries", -1, 1),
            call("rq.jobs.finished.queries"),
        ]
        incr.assert_has_calls(calls)

    @patch("rq.Worker.execute_job")
    def test_worker_records_failure_metrics(self, _, incr):
        """
        Force superclass execute_job to do nothing and set status to JobStatus.Failed to simulate query failure
        """
        query = self.factory.create_query()

        with Connection(rq_redis_connection):
            job = enqueue_query(
                query.query_text,
                query.data_source,
                query.user_id,
                False,
                None,
                {"Username": "Patrick", "query_id": query.id},
            )
            job.set_status(JobStatus.FAILED)

            Worker(["queries"]).work(max_jobs=1)

        calls = [
            call("rq.jobs.running.queries"),
            call("rq.jobs.started.queries"),
            call("rq.jobs.running.queries", -1, 1),
            call("rq.jobs.failed.queries"),
        ]
        incr.assert_has_calls(calls)


@patch("statsd.StatsClient.incr")
class TestQueueMetrics(BaseTestCase):
    def tearDown(self):
        with Connection(rq_redis_connection):
            for queue_name in default_queues:
                Queue(queue_name).empty()

    def test_enqueue_query_records_created_metric(self, incr):
        query = self.factory.create_query()

        with Connection(rq_redis_connection):
            enqueue_query(
                query.query_text,
                query.data_source,
                query.user_id,
                False,
                None,
                {"Username": "Patrick", "query_id": query.id},
            )

        incr.assert_called_with("rq.jobs.created.queries")

    def test_job_delay_records_created_metric(self, incr):
        @job("default", timeout=300)
        def foo():
            pass

        foo.delay()
        incr.assert_called_with("rq.jobs.created.default")


class TestHardLimitingWorkerCancellation:
    """The two-stage SIGINT -> force-kill escalation in stop_executing_job.

    monitor_work_horse calls stop_executing_job once per monitoring interval while a
    cancelled job's work horse is still alive, so escalation happens across passes.
    """

    @staticmethod
    def _worker(horse_pid=4242, graceful_sent=None):
        worker = Mock()
        worker.horse_pid = horse_pid
        worker._graceful_stop_sent_job_id = graceful_sent
        worker._stopped_job_id = None
        return worker

    def test_first_cancel_sends_graceful_sigint(self):
        worker = self._worker()
        job = Mock(id="job-1")
        with patch("redash.tasks.worker.os.kill") as os_kill:
            HardLimitingWorker.stop_executing_job(worker, job)

        # Graceful first: SIGINT lets the query runner cancel the query server-side.
        os_kill.assert_called_once_with(4242, signal.SIGINT)
        worker.kill_horse.assert_not_called()
        assert worker._graceful_stop_sent_job_id == "job-1"

    def test_second_cancel_escalates_to_force_kill(self):
        worker = self._worker(graceful_sent="job-1")
        job = Mock(id="job-1")
        with patch("redash.tasks.worker.os.kill") as os_kill:
            HardLimitingWorker.stop_executing_job(worker, job)

        # Horse ignored SIGINT -> force-kill and mark stopped so the monitor loop reaps it.
        os_kill.assert_not_called()
        worker.kill_horse.assert_called_once_with()
        assert worker._stopped_job_id == "job-1"

    def test_new_job_is_not_force_killed_by_stale_state(self):
        # Tracking from a previous job must not immediately force-kill a different one.
        worker = self._worker(graceful_sent="old-job")
        job = Mock(id="new-job")
        with patch("redash.tasks.worker.os.kill") as os_kill:
            HardLimitingWorker.stop_executing_job(worker, job)

        os_kill.assert_called_once_with(4242, signal.SIGINT)
        worker.kill_horse.assert_not_called()
        assert worker._graceful_stop_sent_job_id == "new-job"

    def test_monitor_work_horse_resets_graceful_stop_state(self):
        # A new job must start from the graceful SIGINT even if a prior job left the flag
        # set, so the per-job state is cleared at the top of monitor_work_horse.
        worker = MagicMock()
        worker._graceful_stop_sent_job_id = "old-job"
        worker.wait_for_horse.return_value = (123, os.EX_OK, None)

        HardLimitingWorker.monitor_work_horse(worker, Mock(id="job-x"), MagicMock())

        assert worker._graceful_stop_sent_job_id is None
