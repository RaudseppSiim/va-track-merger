"""Disk cache for the expensive part: decoding a whole movie into a signal.

Keyed on (path, size, mtime, kind, stream) so re-analysing with different
window settings is instant and only a changed file forces a re-decode.
"""
from __future__ import annotations

import hashlib
import os
from typing import Callable

import numpy as np

WORK_DIR = os.environ.get("WORK_DIR", os.path.join(os.getcwd(), "work"))
CACHE_DIR = os.path.join(WORK_DIR, "cache")


def _key(path: str, kind: str, stream: int) -> str:
    try:
        st = os.stat(path)
        stamp = f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        stamp = "0:0"
    raw = f"{os.path.abspath(path)}|{stamp}|{kind}|{stream}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def get_or_build(path: str, kind: str, stream: int, build: Callable[[], np.ndarray]) -> np.ndarray:
    os.makedirs(CACHE_DIR, exist_ok=True)
    fname = os.path.join(CACHE_DIR, f"{_key(path, kind, stream)}-{kind}.npy")
    if os.path.exists(fname):
        try:
            return np.load(fname)
        except Exception:
            os.remove(fname)

    data = build()
    # np.save appends ".npy" unless the name already ends in it, so the temp
    # name has to carry the suffix or the rename below looks for the wrong file
    tmp = fname + ".tmp.npy"
    np.save(tmp, data)
    os.replace(tmp, fname)
    return data


def get_or_build_pair(path: str, kind: str, stream: int, build):
    """Same as get_or_build for functions returning two arrays."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    fname = os.path.join(CACHE_DIR, f"{_key(path, kind, stream)}-{kind}.npz")
    if os.path.exists(fname):
        try:
            with np.load(fname) as z:
                return z["lo"], z["hi"]
        except Exception:
            os.remove(fname)

    lo, hi = build()
    tmp = fname + ".tmp.npz"
    np.savez_compressed(tmp, lo=lo, hi=hi)
    os.replace(tmp, fname)
    return lo, hi


def clear() -> int:
    if not os.path.isdir(CACHE_DIR):
        return 0
    n = 0
    for name in os.listdir(CACHE_DIR):
        try:
            os.remove(os.path.join(CACHE_DIR, name))
            n += 1
        except OSError:
            pass
    return n
