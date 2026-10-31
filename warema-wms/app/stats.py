"""Radio link statistics per device (poll success, retries, response time) over the last hour."""

from collections import deque

WINDOW_S = 3600


class LinkStats:
    def __init__(self, window=WINDOW_S):
        self.window = window
        self.polls = deque()        # (timestamp, ok, attempts, latency_ms)
        self.offline_events = 0

    def record(self, now, ok, attempts, latency_ms=None):
        self.polls.append((now, ok, attempts, latency_ms))
        self._prune(now)

    def _prune(self, now):
        while self.polls and self.polls[0][0] < now - self.window:
            self.polls.popleft()

    def count(self, now):
        self._prune(now)
        return len(self.polls)

    def reach(self, now):
        """Share of polls answered in the window (%), None without polls."""
        self._prune(now)
        if not self.polls:
            return None
        return round(100 * sum(1 for p in self.polls if p[1]) / len(self.polls), 1)

    def retries(self, now):
        """Extra attempts (beyond the first) sent in the window."""
        self._prune(now)
        return sum(max(p[2] - 1, 0) for p in self.polls)

    def latency(self, now):
        """Mean time from sending to the answer (ms) of answered polls, None without any."""
        self._prune(now)
        values = [p[3] for p in self.polls if p[1] and p[3] is not None]
        return round(sum(values) / len(values)) if values else None
