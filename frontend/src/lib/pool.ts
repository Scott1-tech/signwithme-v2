/**
 * Run `worker` over `items` with at most `limit` in flight at once.
 *
 * Bulk signing issues one request per slot, so a contract with dozens of
 * signature fields must not open dozens of sockets at the same time -- and it
 * must not crawl through them strictly one after another either. The worker is
 * expected to swallow its own failures: this helper never rejects, so one bad
 * slot cannot abort the rest of the run.
 */
export async function runPool<T>(
  items: readonly T[],
  limit: number,
  worker: (item: T, index: number) => Promise<void>,
): Promise<void> {
  let cursor = 0;
  const lanes = Math.max(1, Math.min(limit, items.length));
  const runners = Array.from({ length: lanes }, async () => {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      await worker(items[index], index);
    }
  });
  await Promise.all(runners);
}
