import sys
import queue
import random
import atexit
import weakref
import threading
import time
from concurrent.futures.thread import ThreadPoolExecutor, _base, _WorkItem, _python_exit, _threads_queues

########################################################################################################################
#                                                Global variables                                                      #
########################################################################################################################

NULL_ENTRY = (sys.maxsize, _WorkItem(None, None, (), {}))
_shutdown = False

########################################################################################################################
#                                           Before system exit procedure                                               #
########################################################################################################################


def python_exit():
    """

    Cleanup before system exit

    """
    global _shutdown
    _shutdown = True
    items = list(_threads_queues.items())
    for t, q in items:
        q.put(NULL_ENTRY)
    for t, q in items:
        t.join()

# change default cleanup


atexit.unregister(_python_exit)
atexit.register(python_exit)

########################################################################################################################
#                                               Worker implementation                                                  #
########################################################################################################################


def _worker(executor_reference, work_queue):
    """

    Worker

    :param executor_reference: executor function
    :type executor_reference: callable
    :param work_queue: work queue
    :type work_queue: queue.PriorityQueue

    """
    try:
        while True:
            work_item = work_queue.get(block=True)
            if work_item[0] != sys.maxsize:
                work_item = work_item[1]
                work_item.run()
                del work_item
                continue
            executor = executor_reference()
            if _shutdown or executor is None or executor._shutdown:
                work_queue.put(NULL_ENTRY)
                return
            del executor
    except BaseException:
        _base.LOGGER.critical('Exception in worker', exc_info=True)


########################################################################################################################
#                           Little hack of ThreadPoolExecutor from concurrent.futures.thread                           #
########################################################################################################################


class PriorityThreadPoolExecutor(ThreadPoolExecutor):
    class _PriorityWorkItem(_WorkItem):
        def __init__(self, f, fn, *args, **kwargs):
            super(PriorityThreadPoolExecutor._PriorityWorkItem, self).__init__(f, fn, *args, **kwargs)
            self._time = time.time()

        def __lt__(self, other):
            return self._time < other._time


    """

    Thread pool executor with priority queue (priorities must be different, lowest first)

    """
    def __init__(self, max_workers=None, thread_name_prefix='', initializer=None, initargs=()):
        """

        Initializes a new PriorityThreadPoolExecutor instance

        :param max_workers: the maximum number of threads that can be used to execute the given calls
        :type max_workers: int

        """
        super(PriorityThreadPoolExecutor, self).__init__(max_workers = max_workers,
                                                         thread_name_prefix = thread_name_prefix,
                                                         initializer = initializer,
                                                         initargs = initargs)
        self._work_queue = queue.PriorityQueue()

    # ------------------------------------------------------------------------------------------------------------------

    def submit(self, priority, fn, *args, **kwargs):
        """
        Sending the function to the execution queue

        :param priority: priority of the task
        :type priority: int
        :param fn: function being executed
        :type fn: callable
        :param args: function's positional arguments
        :param kwargs: function's keywords arguments
        :return: future instance
        :rtype: _base.Future
        """
        with self._shutdown_lock:
            if self._shutdown:
                raise RuntimeError('cannot schedule new futures after shutdown')

            f = _base.Future()
            w = PriorityThreadPoolExecutor._PriorityWorkItem(f, fn, args, kwargs)

            self._work_queue.put((priority, w))
            self._adjust_thread_count()
            return f

    # ------------------------------------------------------------------------------------------------------------------

    def _adjust_thread_count(self):
        """

        Attempt to start a new thread

        """
        def weak_ref_cb(_, q=self._work_queue):
            q.put(NULL_ENTRY)
        no_threads = len(self._threads)
        if no_threads < self._max_workers:
            t = threading.Thread(target=_worker,
                                 name = "%s-%d" % (self._thread_name_prefix, no_threads),
                                 args=(weakref.ref(self, weak_ref_cb),
                                       self._work_queue))
            t.daemon = True
            t.start()
            self._threads.add(t)
            _threads_queues[t] = self._work_queue

    # ------------------------------------------------------------------------------------------------------------------

    def shutdown(self, wait=True):
        """

        Pool shutdown

        :param wait: if True wait for all threads to complete
        :type wait: bool

        """
        with self._shutdown_lock:
            self._shutdown = True
            self._work_queue.put(NULL_ENTRY)
        if wait:
            for t in self._threads:
                t.join()
