"""executing/ - the scheduler/executor: the consumption half of specseed.

The tracking + scheduling layers PRODUCE work: a remote (source of truth) is
polled on an interval, synced into a local mirror, and the differences become
typed tasks on the DB queue. This package CONSUMES that queue.

Threading model (deliberate, three levels):

* the **main thread** (``run.py``) builds a :class:`~.scheduler.Scheduler`,
  installs signal handlers, starts the scheduler, and then only waits for a stop
  signal. It is never blocked by an agent run.
* the **scheduler thread** (daemon) owns the poll -> sync -> drain loop and can be
  paused/stopped through Events without blocking the main thread.
* each **agent run** happens in its own worker thread so it can be stopped on a
  timer (default 6h) or cooperatively cancelled (e.g. when the remote entry it was
  working on is closed) without wedging the scheduler.

Only Python stdlib is used throughout.
"""
