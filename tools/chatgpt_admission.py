"""Bounded admission for the one ChatGPT browser worker.

Owned by the bridge process, so all callers share the same capacity and seat
deduplication. Cancellation never frees a running browser slot prematurely.
"""
import threading
import time
import uuid


class Admission:
    def __init__(self, capacity=2, clock=time.time):
        self.capacity = max(1, int(capacity))
        self.clock = clock
        self.lock = threading.Condition()
        self.waiters = []
        self.max_waiters = 11
        self.max_wait_s = 300.0
        self.active = {}
        self.counts = dict(accepted=0, rejected=0, completed=0, expired=0, cancelled=0)

    def reserve(self, job, skip_if_busy=False, external_busy=False, wait_for_slot=False):
        # Waiting belongs to the HTTP admission lane, never the browser queue.
        # Trader pairs and opted-in direct chats share FIFO; legacy skip callers stay immediate.
        with self.lock:
            now = self.clock()
            key = job.setdefault('request_id', uuid.uuid4().hex)
            seat = job.get('seat') or ''
            end = min(float(job.get('deadline') or 0) - 65, now + self.max_wait_s)
            waiter = None
            wall_end = time.monotonic() + self.max_wait_s
            try:
                while True:
                    now = self.clock()
                    all_jobs = list(self.active.values()) + self.waiters
                    others = [j for j in all_jobs if j is not job]
                    # Later waiters cannot invalidate an older FIFO waiter.
                    older_waiters = []
                    for queued in self.waiters:
                        if queued is job:break
                        older_waiters.append(queued)
                    prior_jobs = list(self.active.values()) + older_waiters
                    seat_jobs = [j for j in prior_jobs if j is not job and seat and j.get('seat') == seat]
                    # Distinct learning/trader activities may WAIT for this seat.
                    # They never share an admitted slot; same-workload and request
                    # duplicates retain their immediate rejection contract.
                    serial_learning = bool(wait_for_slot and not skip_if_busy and seat_jobs
                        and job.get('workload') in ('trader', 'learning')
                        and all(j.get('workload') in ('trader', 'learning')
                                and j.get('workload') != job.get('workload') for j in seat_jobs))
                    seat_active = any(seat and j.get('seat') == seat for j in self.active.values())
                    reason = None
                    if float(job.get('deadline') or 0) - now < 65:
                        reason = 'insufficient deadline budget'
                    elif key in self.active or any(j.get('request_id') == key for j in others):
                        reason = 'duplicate request already admitted or waiting'
                    elif (seat_jobs and not serial_learning
                          and not (wait_for_slot and not skip_if_busy and job.get('workload')=='chat')):
                        reason = 'seat already admitted or waiting'
                    elif (skip_if_busy and (self.active or external_busy or self.waiters)):
                        reason = 'bridge capacity full'
                    if reason:
                        self.counts['rejected'] += 1
                        return False, reason
                    first = not self.waiters or self.waiters[0] is job
                    if len(self.active) < self.capacity and first and not (serial_learning and seat_active):
                        job.update(state='queued', accepted_at=now, done=threading.Event(),
                                   cancelled=threading.Event(), result=None)
                        self.active[key] = job
                        self.counts['accepted'] += 1
                        return True, None
                    if not wait_for_slot or skip_if_busy or job.get('workload') not in ('trader', 'chat', 'learning'):
                        self.counts['rejected'] += 1
                        return False, 'bridge capacity full'
                    if waiter is None:
                        if len(self.waiters) >= self.max_waiters:
                            self.counts['rejected'] += 1
                            return False, 'admission waiting lane full'
                        waiter = job
                        job['waiting_at'] = now
                        self.waiters.append(job)
                    job['waiting_for'] = 'same-seat learning/trader release' if serial_learning and seat_active else 'capacity/FIFO'
                    left = min(end - now, wall_end - time.monotonic())
                    if left <= 0:
                        self.counts['rejected'] += 1
                        return False, 'bounded admission wait expired; nothing submitted'
                    self.lock.wait(timeout=min(left, 1.0))
            finally:
                if waiter is not None:
                    self.waiters.remove(waiter)
                    self.lock.notify_all()

    def start(self, job):
        with self.lock:
            if job['request_id'] not in self.active:
                return False
            if job['cancelled'].is_set() or float(job['deadline']) - self.clock() < 60:
                return False
            job['state'] = 'running'
            job['started_at'] = self.clock()
            return True

    def cancel(self, job):
        with self.lock:
            if job['request_id'] not in self.active:
                return
            job['cancelled'].set()
            # The worker owns cleanup, including queued jobs. A caller timeout
            # must never create a second running browser slot.

    def finish(self, job, result):
        with self.lock:
            if job['request_id'] not in self.active:
                return
            now = self.clock()
            if job['cancelled'].is_set():
                result = dict(ok=False, error='request cancelled; result discarded')
                self.counts['cancelled'] += 1
            elif now >= float(job['deadline']):
                result = dict(ok=False, error='request deadline expired; result discarded')
                self.counts['expired'] += 1
            elif not result.get('ok') and 'expired' in str(result.get('error', '')):
                self.counts['expired'] += 1
            else:
                self.counts['completed'] += 1
            job['result'] = dict(result, request_id=job['request_id'], seat=job.get('seat'),
                                 accepted_at=job['accepted_at'],
                                 started_at=job.get('started_at'), finished_at=now)
            job['state'] = 'finished'
            self.active.pop(job['request_id'], None)
            job['done'].set()
            self.lock.notify_all()

    def snapshot(self):
        with self.lock:
            return dict(protocol="bounded_seat_admission_v1", capacity=self.capacity, inflight=len(self.active),
                        waiting=len(self.waiters), max_waiters=self.max_waiters, max_wait_s=self.max_wait_s,
                        distinct_owner_chats_share_seat=True, same_seat_learning_trader_serialized=True,
                        wait_policy="FIFO trader/chat/learning admission; learning/trader seat serialization; no browser concurrency increase",
                        waiting_requests=[dict(request_id=j.get("request_id"), seat=j.get("seat"), workload=j.get("workload"), waiting_for=j.get("waiting_for"), waiting_at=j.get("waiting_at"), deadline=j.get("deadline")) for j in self.waiters],
                        running=sum(j['state'] == 'running' for j in self.active.values()),
                        queued=sum(j['state'] == 'queued' for j in self.active.values()),
                        requests=[dict(request_id=j['request_id'], seat=j.get('seat'),
                                       workload=j.get('workload'), state=j['state'],
                                       accepted_at=j['accepted_at'], deadline=j['deadline'])
                                  for j in self.active.values()], counts=dict(self.counts))
