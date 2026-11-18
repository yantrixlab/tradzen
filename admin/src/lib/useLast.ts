import { useEffect, useState } from 'react';

/**
 * The last value `current` held that was not null: the row of a list that was opened last. `current` goes back to null when the details
 * are closed, but the row stays marked (class "row-picked" in index.css) until another one is opened.
 */
export function useLast<T>(current: T | null): T | null {
  const [last, setLast] = useState<T | null>(current);
  useEffect(() => { if (current !== null) setLast(current); }, [current]);
  return last;
}
