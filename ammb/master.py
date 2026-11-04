"""Matchering pass (reference EQ match + Hyrax brickwall limiter)."""

from __future__ import annotations

import time
from pathlib import Path


def run_matchering(target: str | Path, reference: str | Path, out_path: str | Path,
                   also_unlimited: bool = False, quiet: bool = False) -> dict:
    import matchering as mg

    if not quiet:
        mg.log(lambda msg: print(f"    [matchering] {msg}"))
    results = [mg.pcm24(str(out_path))]
    unlimited = None
    if also_unlimited:
        p = Path(out_path)
        unlimited = p.with_name(p.stem + "_nolimiter.wav")
        results.append(mg.Result(str(unlimited), subtype="FLOAT", use_limiter=False))
    t0 = time.time()
    mg.process(target=str(target), reference=str(reference), results=results)
    return {"seconds": round(time.time() - t0, 2), "master": str(out_path),
            "master_nolimiter": str(unlimited) if unlimited else None}
