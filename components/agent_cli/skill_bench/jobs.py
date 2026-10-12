"""case × arm × repeat task list. Failed records do not overwrite other IDs."""

from __future__ import annotations

import hashlib
import random
from dataclasses import asdict, dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class BenchJob:
    job_id: str
    case_id: str
    arm: str
    repeat: int
    split: str

    def as_dict(self) -> dict:
        return asdict(self)


def job_id(arm: str, case_id: str, repeat: int) -> str:
    raw = f"{arm}__{case_id}__r{repeat}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{raw}__{digest}"


def build_jobs(
    *,
    case_ids: Iterable[str],
    arms: Iterable[str],
    repeats: int,
    splits: dict,
    seed: int = 20260315,
    interleave: bool = True,
) -> List[BenchJob]:
    jobs = []
    for case_id in case_ids:
        split = splits.get(case_id, "")
        for arm in arms:
            for repeat in range(1, repeats + 1):
                jobs.append(BenchJob(
                    job_id=job_id(arm, case_id, repeat),
                    case_id=case_id,
                    arm=arm,
                    repeat=repeat,
                    split=split,
                ))
    if interleave:
        rng = random.Random(seed)
        rng.shuffle(jobs)
    return jobs
