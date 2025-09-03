import { useEffect, useState, useTransition, useRef } from "react";
import { useDebouncedValue } from "./useDebouncedValue";
import { searchItems } from "../services/searchService";

export function useItemsSearch(initial = {}) {
  const [query, setQuery] = useState(initial.q ?? "");
  const [filters, setFilters] = useState(initial.filters ?? {});
  const [page, setPage] = useState(initial.page ?? 1);
  const [pageSize, setPageSize] = useState(initial.pageSize ?? 20);

  const debouncedQuery = useDebouncedValue(query, 300);
  const [isPending, startTransition] = useTransition();
  const [state, setState] = useState({ loading: false, error: null, items: [], total: 0 });
  const mountedRef = useRef(true);

  useEffect(() => () => { mountedRef.current = false; }, []);

  useEffect(() => {
    let cancelled = false;
    setState(s => ({ ...s, loading: true, error: null }));

    startTransition(async () => {
      try {
        const data = await searchItems({ q: debouncedQuery, ...filters, page, pageSize });
        if (!mountedRef.current || cancelled) return;
        setState({
          loading: false,
          error: null,
          items: data.items ?? data.results ?? data,
          total: data.total ?? 0,
        });
      } catch (err) {
        if (!mountedRef.current || cancelled) return;
        if (err.name === "CanceledError" || err.name === "AbortError") return; // fue cancelada
        setState(s => ({ ...s, loading: false, error: err }));
      }
    });

    return () => { cancelled = true; };
  }, [debouncedQuery, filters, page, pageSize]);

  return {
    ...state,
    query, setQuery,
    filters, setFilters,
    page, setPage,
    pageSize, setPageSize,
    isPending,
  };
}
