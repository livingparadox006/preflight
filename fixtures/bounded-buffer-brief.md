# CSE 220 — Assignment 3: Bounded Buffer

**Due: 2026-11-06 at 23:59.** Submit a single `.zip` containing your source files and a `README.md`.

Implement a bounded producer–consumer buffer in C using POSIX threads.

Requirements:

1. The buffer must hold at most **8 items**.
2. Producers must block when the buffer is full; consumers must block when it is empty.
3. Use a mutex and condition variables; do not use busy-waiting.
4. The program must accept `--producers N --consumers M --items K` and validate its arguments.
5. Demonstrate no lost or duplicated items across at least **10,000** items.
6. Include a stress test that runs for at least **30 seconds**.
7. The README must explain how to build and run the program, and state the observed throughput.

You may not use a pre-built thread-safe queue library.
Do not include compiled binaries in the zip.
Late submissions lose 10% per day.
