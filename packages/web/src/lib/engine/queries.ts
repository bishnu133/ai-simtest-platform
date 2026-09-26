"use client";

import { useQuery } from "@tanstack/react-query";
import { engine } from "./client";
import { isActive } from "./runs";

/** Every run the engine knows, newest first. Polls faster while one is active. */
export function useRuns() {
  return useQuery({
    queryKey: ["runs"],
    queryFn: async () => (await engine.listSimulations()).simulations,
    refetchInterval: (query) => (query.state.data?.some(isActive) ? 4000 : 20000),
  });
}

/** Whether the engine answers; the options call is cheap and always allowed. */
export function useEngineHealth() {
  return useQuery({
    queryKey: ["engine-health"],
    queryFn: engine.getOptions,
    refetchInterval: 30000,
    retry: false,
  });
}
