"""Turn ReadResults into plain-language outcomes for the summary table."""

import numpy as np

from stale_cube import data
from stale_cube.readers import ReadResult

EXPECTED = np.array([data.expected_value(t) for t in range(data.N_TIMES)], dtype=float)
T_OVERWRITE, T_DELETE = 40, 70


def changed_step(change: str) -> int:
    return T_OVERWRITE if change == "overwrite" else T_DELETE


def outcome(res: ReadResult, change: str) -> str:
    if res.error is not None:
        return f"RAISES ({res.error_kind or res.error})"
    t = changed_step(change)
    v = res.values
    others = np.delete(np.arange(data.N_TIMES), t)
    bad_others = int((v[others] != EXPECTED[others]).sum())
    if np.isnan(v[t]):
        if t in res.skipped:
            at_t = "NaN (flagged in mask)"
        elif res.mode == "check-then-repair":
            at_t = "NaN (ref removed by repair)"
        else:
            at_t = "NaN (not flagged)"
    elif v[t] == data.expected_value(t, data.REPUBLISH_OFFSET):
        at_t = "republished value"
    elif v[t] == EXPECTED[t]:
        at_t = "original value"
    else:
        at_t = f"WRONG value {v[t]:.0f}"
    tail = "" if bad_others == 0 else f"; {bad_others} other steps wrong"
    return f"{100 - 1 - bad_others}/99 others ok, t={t}: {at_t}{tail}"
