/*
 * Linux-only proof of concept: enumerate once, then compare serial statx with
 * batched IORING_OP_STATX on the same paths. Includes ignored/build files.
 * No liburing dependency, writes, watches, or changes to the GC implementation.
 *
 * mkdir -p "${TMPDIR:-/tmp}/agents"
 * bench_dir=$(mktemp -d "${TMPDIR:-/tmp}/agents/XXXXXXXXXX")
 * cc -O2 -std=c11 -Wall -Wextra \
 *   tools/benchmark-gc-io-uring.c -o "$bench_dir/gc-uring"
 * "$bench_dir/gc-uring" WORKTREE [QUEUE_DEPTH [ROUNDS]]
 * Enumeration and ring setup are measured separately from timestamp requests.
 * Each method computes the exact maximum file mtime, following file symlinks
 * but not directory symlinks. .git exclusions match the existing GC scan.
 * Enumeration includes symlink classification. Boundary counts cover only the
 * timestamp stage. This PoC omits GC's empty-tree fallback and short circuits.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <linux/io_uring.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

struct paths {
  char **items;
  size_t count, capacity, directories;
};
struct result {
  size_t ok, errors, calls;
  bool have_time;
  int64_t seconds;
  uint32_t nanoseconds;
};
struct ring {
  int fd;
  void *sq, *cq;
  size_t sq_size, cq_size, entries_size;
  struct io_uring_sqe *entries;
  unsigned *sq_tail, *sq_mask, *sq_array;
  unsigned *cq_head, *cq_tail, *cq_mask;
  struct io_uring_cqe *completions;
};

static void fail(const char *message) {
  perror(message);
  exit(EXIT_FAILURE);
}
static void *allocate(size_t bytes) {
  void *value = malloc(bytes);
  if (!value)
    fail("malloc");
  return value;
}
static double now(void) {
  struct timespec value;
  if (clock_gettime(CLOCK_MONOTONIC, &value))
    fail("clock_gettime");
  return value.tv_sec + value.tv_nsec / 1e9;
}
static void add_path(struct paths *paths, char *path) {
  if (paths->count == paths->capacity) {
    paths->capacity = paths->capacity ? paths->capacity * 2 : 1024;
    void *items = realloc(paths->items, paths->capacity * sizeof(char *));
    if (!items)
      fail("realloc");
    paths->items = items;
  }
  paths->items[paths->count++] = path;
}
static void enumerate(struct paths *paths, const char *root, bool top) {
  DIR *directory = opendir(root);
  if (!directory)
    fail("opendir");
  paths->directories++;
  for (;;) {
    errno = 0;
    struct dirent *entry = readdir(directory);
    if (!entry) {
      if (errno)
        fail("readdir");
      break;
    }
    if (!strcmp(entry->d_name, ".") || !strcmp(entry->d_name, ".."))
      continue;
    char *path = allocate(strlen(root) + strlen(entry->d_name) + 2);
    sprintf(path, "%s/%s", root, entry->d_name);
    bool is_directory = entry->d_type == DT_DIR;
    bool is_link = entry->d_type == DT_LNK;
    if (entry->d_type == DT_UNKNOWN) {
      struct stat metadata;
      if (!fstatat(dirfd(directory), entry->d_name, &metadata,
                   AT_SYMLINK_NOFOLLOW)) {
        is_directory = S_ISDIR(metadata.st_mode);
        is_link = S_ISLNK(metadata.st_mode);
      }
    }
    if (is_link) {
      struct stat target;
      if (!fstatat(dirfd(directory), entry->d_name, &target, 0))
        is_directory = S_ISDIR(target.st_mode);
    }
    if (is_directory) {
      if (!is_link && strcmp(entry->d_name, ".git"))
        enumerate(paths, path, false);
      free(path);
    } else if (top && !strcmp(entry->d_name, ".git")) {
      free(path);
    } else {
      add_path(paths, path);
    }
  }
  closedir(directory);
}
static void record(struct result *result, const struct statx *metadata,
                   int status) {
  if (status < 0 || !(metadata->stx_mask & STATX_MTIME)) {
    result->errors++;
    return;
  }
  result->ok++;
  int64_t seconds = metadata->stx_mtime.tv_sec;
  uint32_t nanos = metadata->stx_mtime.tv_nsec;
  if (!result->have_time || seconds > result->seconds ||
      (seconds == result->seconds && nanos > result->nanoseconds)) {
    result->have_time = true;
    result->seconds = seconds;
    result->nanoseconds = nanos;
  }
}
static struct result serial(const struct paths *paths) {
  struct result result = {0};
  for (size_t i = 0; i < paths->count; i++) {
    struct statx metadata = {0};
    int status = statx(AT_FDCWD, paths->items[i], 0, STATX_MTIME, &metadata);
    result.calls++;
    record(&result, &metadata, status < 0 ? -errno : 0);
  }
  return result;
}
static void setup(struct ring *ring, unsigned depth) {
  struct io_uring_params parameters = {0};
  ring->fd = syscall(__NR_io_uring_setup, depth, &parameters);
  if (ring->fd < 0)
    fail("io_uring_setup");
  ring->sq_size =
      parameters.sq_off.array + parameters.sq_entries * sizeof(unsigned);
  ring->cq_size = parameters.cq_off.cqes +
                  parameters.cq_entries * sizeof(struct io_uring_cqe);
  if (parameters.features & IORING_FEAT_SINGLE_MMAP) {
    if (ring->sq_size < ring->cq_size)
      ring->sq_size = ring->cq_size;
    ring->cq_size = ring->sq_size;
  }
  ring->sq = mmap(NULL, ring->sq_size, PROT_READ | PROT_WRITE,
                  MAP_SHARED | MAP_POPULATE, ring->fd, IORING_OFF_SQ_RING);
  if (ring->sq == MAP_FAILED)
    fail("mmap SQ");
  ring->cq = ring->sq;
  if (!(parameters.features & IORING_FEAT_SINGLE_MMAP)) {
    ring->cq = mmap(NULL, ring->cq_size, PROT_READ | PROT_WRITE,
                    MAP_SHARED | MAP_POPULATE, ring->fd, IORING_OFF_CQ_RING);
    if (ring->cq == MAP_FAILED)
      fail("mmap CQ");
  }
  ring->entries_size = parameters.sq_entries * sizeof(struct io_uring_sqe);
  ring->entries = mmap(NULL, ring->entries_size, PROT_READ | PROT_WRITE,
                       MAP_SHARED | MAP_POPULATE, ring->fd, IORING_OFF_SQES);
  if (ring->entries == MAP_FAILED)
    fail("mmap SQEs");
  char *sq = ring->sq, *cq = ring->cq;
  ring->sq_tail = (unsigned *)(sq + parameters.sq_off.tail);
  ring->sq_mask = (unsigned *)(sq + parameters.sq_off.ring_mask);
  ring->sq_array = (unsigned *)(sq + parameters.sq_off.array);
  ring->cq_head = (unsigned *)(cq + parameters.cq_off.head);
  ring->cq_tail = (unsigned *)(cq + parameters.cq_off.tail);
  ring->cq_mask = (unsigned *)(cq + parameters.cq_off.ring_mask);
  ring->completions = (struct io_uring_cqe *)(cq + parameters.cq_off.cqes);
}
static struct result batched(struct ring *ring, const struct paths *paths,
                             unsigned depth) {
  struct result result = {0};
  struct statx *metadata = allocate(depth * sizeof(struct statx));
  for (size_t base = 0; base < paths->count; base += depth) {
    unsigned count = paths->count - base < depth ? paths->count - base : depth;
    memset(metadata, 0, count * sizeof(struct statx));
    unsigned tail = __atomic_load_n(ring->sq_tail, __ATOMIC_RELAXED);
    for (unsigned i = 0; i < count; i++) {
      unsigned slot = (tail + i) & *ring->sq_mask;
      struct io_uring_sqe *entry = &ring->entries[slot];
      memset(entry, 0, sizeof(*entry));
      entry->opcode = IORING_OP_STATX;
      entry->fd = AT_FDCWD;
      entry->addr = (uintptr_t)paths->items[base + i];
      entry->off = (uintptr_t)&metadata[i];
      entry->len = STATX_MTIME;
      entry->user_data = i;
      ring->sq_array[slot] = slot;
    }
    __atomic_store_n(ring->sq_tail, tail + count, __ATOMIC_RELEASE);
    unsigned submitted = 0, completed = 0;
    while (completed < count) {
      /* Submit without waiting in case the kernel accepts a partial batch.
       * Once all requests are submitted, wait for the whole remainder. */
      bool waiting = submitted == count;
      int status = syscall(__NR_io_uring_enter, ring->fd, count - submitted,
                           waiting ? count - completed : 0,
                           waiting ? IORING_ENTER_GETEVENTS : 0, NULL, 0);
      result.calls++;
      if (status < 0) {
        if (errno == EINTR)
          continue;
        fail("io_uring_enter");
      }
      submitted += status;
      unsigned head = __atomic_load_n(ring->cq_head, __ATOMIC_RELAXED);
      unsigned end = __atomic_load_n(ring->cq_tail, __ATOMIC_ACQUIRE);
      while (head != end) {
        struct io_uring_cqe *entry = &ring->completions[head & *ring->cq_mask];
        if (entry->user_data >= count) {
          fprintf(stderr, "Unexpected completion identifier\n");
          exit(EXIT_FAILURE);
        }
        record(&result, &metadata[entry->user_data], entry->res);
        head++;
        completed++;
      }
      __atomic_store_n(ring->cq_head, head, __ATOMIC_RELEASE);
    }
  }
  free(metadata);
  return result;
}
static int compare(const void *a, const void *b) {
  double left = *(const double *)a, right = *(const double *)b;
  return (left > right) - (left < right);
}
static unsigned positive(const char *text, unsigned maximum) {
  char *end;
  unsigned long value = strtoul(text, &end, 10);
  if (!*text || *end || value < 1 || value > maximum) {
    fprintf(stderr, "Invalid positive argument: %s\n", text);
    exit(EXIT_FAILURE);
  }
  return value;
}
int main(int argc, char **argv) {
  if (argc < 2 || argc > 4) {
    fprintf(stderr, "Usage: %s WORKTREE [QUEUE_DEPTH [ROUNDS]]\n", argv[0]);
    return EXIT_FAILURE;
  }
  unsigned depth = argc > 2 ? positive(argv[2], 4096) : 64;
  unsigned rounds = argc > 3 ? positive(argv[3], 99) : 7;
  struct paths paths = {0};
  double start = now();
  enumerate(&paths, argv[1], true);
  printf("Enumeration: %.6fs, %zu files, %zu directories\n", now() - start,
         paths.count, paths.directories);
  struct ring ring = {0};
  start = now();
  setup(&ring, depth);
  printf("Ring setup: %.6fs, depth=%u\n", now() - start, depth);
  double *serial_times = allocate(rounds * sizeof(double));
  double *batch_times = allocate(rounds * sizeof(double));
  bool matches = true;
  struct result results[2] = {0};
  for (unsigned i = 0; i < rounds; i++) {
    for (unsigned j = 0; j < 2; j++) {
      unsigned mode = (i + j) % 2;
      start = now();
      results[mode] = mode ? batched(&ring, &paths, depth) : serial(&paths);
      double elapsed = now() - start;
      (mode ? batch_times : serial_times)[i] = elapsed;
      printf("Round %u %s: %.6fs, %zu boundary calls, %zu ok, %zu errors\n",
             i + 1, mode ? "io_uring" : "serial", elapsed, results[mode].calls,
             results[mode].ok, results[mode].errors);
      fflush(stdout);
    }
    bool same = results[0].ok == results[1].ok &&
                results[0].errors == results[1].errors &&
                results[0].have_time == results[1].have_time &&
                results[0].seconds == results[1].seconds &&
                results[0].nanoseconds == results[1].nanoseconds;
    matches &= same;
  }
  qsort(serial_times, rounds, sizeof(double), compare);
  qsort(batch_times, rounds, sizeof(double), compare);
  double serial_median =
      (serial_times[(rounds - 1) / 2] + serial_times[rounds / 2]) / 2;
  double batch_median =
      (batch_times[(rounds - 1) / 2] + batch_times[rounds / 2]) / 2;
  printf("Median timestamp stage: serial %.6fs; io_uring %.6fs; ratio %.3fx\n",
         serial_median, batch_median, serial_median / batch_median);
  printf("Maximum mtime and result counts match each round: %s\n",
         matches ? "yes" : "no");
  printf("Maximum file mtime: serial %" PRId64 ".%09u; io_uring %" PRId64
         ".%09u\n",
         results[0].seconds, results[0].nanoseconds, results[1].seconds,
         results[1].nanoseconds);
  munmap(ring.entries, ring.entries_size);
  if (ring.cq != ring.sq)
    munmap(ring.cq, ring.cq_size);
  munmap(ring.sq, ring.sq_size);
  close(ring.fd);
  for (size_t i = 0; i < paths.count; i++)
    free(paths.items[i]);
  free(paths.items);
  free(serial_times);
  free(batch_times);
  return matches ? EXIT_SUCCESS : EXIT_FAILURE;
}
