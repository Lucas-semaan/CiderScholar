import { useCallback, useEffect, useState } from "react";

interface RemoteState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

export function useRemoteData<T>(loader: () => Promise<T>) {
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<RemoteState<T>>({
    data: null,
    error: null,
    loading: true,
  });

  useEffect(() => {
    let active = true;
    setState((previous) => ({ ...previous, error: null, loading: true }));
    void loader()
      .then((data) => {
        if (active) setState({ data, error: null, loading: false });
      })
      .catch((error: unknown) => {
        if (active) {
          setState((previous) => ({
            ...previous,
            error: error instanceof Error ? error.message : "Erreur inconnue",
            loading: false,
          }));
        }
      });
    return () => {
      active = false;
    };
  }, [loader, revision]);

  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  return { ...state, refresh };
}
