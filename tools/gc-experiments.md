# GC performance experiments

Recorded on 2026-09-30 against the monorepo. The feature changes are independent
drafts: [recency/cache #14](https://github.com/General-Wisdom/gwt/pull/14) and
[merged-PR retention #15](https://github.com/General-Wisdom/gwt/pull/15).
The experiment branch includes both features through a shared integration base.
Its additional implementation is parallel timestamp scanning; the native
io_uring benchmark remains a standalone proof of concept.

## Parallel scanning

The original serial profile spent 47.9 of 51.2 seconds in filesystem scans.
Ignored dependency and build directories are included because builds are activity.
Recent-path hints avoid full scans only when a current timestamp proves recency.
An old hint never proves that the rest of the tree is old.

One timestamp-only pass across 96 worktrees measured:

| Backend | Seconds |
|---|---:|
| Serial Python | 27.091 |
| Two threads | 34.345 |
| Two processes | 12.001 |
| Four processes | 6.934 |
| Eight processes | 4.829 |

Threads regressed, so the prototype uses processes. The worker count defaults to
the smaller of eight and the CPU count. Only timestamp scans run in workers;
Git checks and mutations stay in the parent. The parent bounds outstanding jobs
and supplies newly learned recency hints to subsequent submissions.

The permanent parallel benchmark uses one worktree/PR snapshot and fixed clock,
rotates worker-count order, disables cache writes and Git index updates, and
compares all plan entries. Three-round medians, excluding the shared 1.298-second
GitHub lookup but including process startup and local Git checks:

| Processes | Median seconds |
|---|---:|
| 1 | 22.584 |
| 2 | 12.186 |
| 4 | 7.864 |
| 8 | 6.341 |

Every comparison produced the same plan entries. An ordinary CLI preview took
6.71 seconds. These results vary with filesystem cache state and other activity.

## Full scans by worktree

The [per-worktree CSV](benchmarks/gc-worktree-timings.csv) records all 96 worktrees,
with file/directory counts, exact scan times, local Git timing components, and
normal parallel scan times. One forced sequential pass examined 5,142,387 file
paths in 718,456 directories. It disabled recency shortcuts but retained normal
GC exclusions and action checks. No cleaning or deletion was performed.

| Work | Seconds |
|---|---:|
| Exact full scans | 29.596 |
| Local Git checks | 1.779 |
| Shared GitHub lookup | 1.104 |
| Entire diagnostic plan | 32.504 |

A subsequent normal eight-process plan took 5.913 seconds, with a 1.364-second
GitHub lookup. The interval from the first worker scan starting to the final scan
finishing was 3.640 seconds. Worker durations overlap and must not be added to
compute wall time. Nine recent worktrees needed 9.407 seconds in the forced full
pass but only 0.081 seconds combined with normal shortcuts. The other 87 trees
accounted for 20.189 seconds of sequential full scans.

## Native and io_uring proof of concept

The standalone Linux C benchmark uses kernel headers and raw io_uring syscalls;
it adds no liburing or Python dependency. It enumerates paths once, then compares
serial statx and batched IORING_OP_STATX requests against the same paths. File
symlinks are followed, directory symlinks are not, and ignored/build files count.
The prototype omits GC's empty-tree fallback and recency shortcuts. Its source
header contains compilation and execution instructions.

On the nourdine-image-sizing-standards worktree, it enumerated 172,431 file paths
and 17,862 directories. Both native methods found the same maximum timestamp and
result counts on every round: 172,430 successful requests and one failed target.
With a queue depth of 256, timestamp requests needed roughly 800–1,000 kernel
entries rather than 172,431 serial calls. Directory enumeration is additional
work and includes symlink classification.

A warm-cache comparison against the actual Python exact scanner measured:

| Implementation | Full scan seconds |
|---|---:|
| Python scanner, median of seven full scans | 0.707 |
| C serial: enumeration plus median timestamp stage | 0.356 |
| C io_uring: enumeration plus median timestamp stage | 0.252 |

In that native pass, enumeration took 0.135 seconds; the median timestamp stages
took 0.221 seconds serially and 0.117 seconds with io_uring. Thus batching reduced
timestamp-stage time by about 1.9 times, while native full-scan totals were about
2.0 and 2.8 times faster than Python. This is one warmed worktree, excludes
interpreter startup and other GC checks, and is not a whole-command comparison.
The normal sandbox rejected io_uring setup; the authorized host benchmark worked.

## Benchmark tools

- `benchmark-gc.py` compares exact baseline scanning, threshold shortcuts, and
  cold/warm path hints using synthetic trees. Its default baseline is the original
  main commit; override it with the baseline option. The original commit is
  `6f764aa4a31218793b8e7f4066ef67c668a832b3`.
- `benchmark-gc-parallel.py` compares process counts on a real repository while
  preserving plan semantics and disabling cache writes and Git index updates.
- `benchmark-gc-io-uring.c` separates enumeration from serial and batched native
  timestamp stages. It is not called by the GC implementation.

Both Python benchmarks use PEP 723 metadata and run with uv. No runtime dependency
was added to gwt. This draft preserves the experiments for further review rather
than integrating the native prototype into normal GC.
