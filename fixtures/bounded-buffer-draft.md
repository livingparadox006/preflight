# Bounded Buffer — Notes

I will implement a circular buffer with a mutex and two condition variables.

The struct has an array of 8 slots, a head, a tail, and a count. Producers lock the mutex and wait on `not_full`. Consumers wait on `not_empty`. Both signal the opposite condition after changing the count.

The main function parses the arguments and starts the threads. I tested it with a few items and it seemed to work.

I still need to write the stress test and measure throughput. The README is not written yet.
