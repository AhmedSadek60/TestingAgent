import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiClient } from "./api/client";
import { createApi, type Api } from "./api/endpoints";
import { ApiError, describeError } from "./api/errors";
import type { Health } from "./api/types";
import { PROJECT_KEY, readStored, TOKEN_KEY, writeStored } from "./lib/storage";

export type AuthPhase = "connecting" | "unreachable" | "needs-token" | "ready";

export interface Session {
  api: Api;
  client: ApiClient;
  phase: AuthPhase;
  health: Health | null;
  /** Why the last sign-in or the connection failed. */
  problem: string | null;
  signIn: (token: string) => Promise<boolean>;
  signOut: () => void;
  retry: () => void;
  authRequired: boolean;
  project: string;
  setProject: (name: string) => void;
}

const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside <SessionProvider>");
  return value;
}

export function useApi(): Api {
  return useSession().api;
}

export function SessionProvider({ children, base = "" }: { children: ReactNode; base?: string }) {
  const tokenRef = useRef<string | null>(readStored("session", TOKEN_KEY));
  const [phase, setPhase] = useState<AuthPhase>("connecting");
  const [health, setHealth] = useState<Health | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [project, setProjectState] = useState<string>(readStored("local", PROJECT_KEY) ?? "default");
  const projectRef = useRef(project);
  const [attempt, setAttempt] = useState(0);

  const dropToken = useCallback(() => {
    tokenRef.current = null;
    writeStored("session", TOKEN_KEY, null);
  }, []);

  const client = useMemo(
    () =>
      new ApiClient({
        base,
        getToken: () => tokenRef.current,
        onUnauthorized: () => {
          dropToken();
          setProblem("The server did not accept the API token. Enter it again.");
          setPhase((current) => (current === "ready" || current === "connecting" ? "needs-token" : current));
        },
      }),
    [base, dropToken],
  );
  const api = useMemo(() => createApi(client), [client]);

  /** The project the person works in must exist before anything is listed for it. A fresh server has none: the default one
   * is created, as the first run would create it. A project that is gone (the store was reset) gives way to the first. */
  const settleProject = useCallback(
    async (signal?: AbortSignal) => {
      const projects = await api.projects(signal);
      const names = projects.map((p) => p.name);
      if (names.includes(projectRef.current)) return;
      let chosen = names[0];
      if (chosen === undefined) {
        try {
          chosen = (await api.createProject({ name: "default", description: "The default project" })).name;
        } catch (error) {
          if (!(error instanceof ApiError && error.status === 409)) throw error;
          chosen = "default";
        }
      }
      projectRef.current = chosen;
      setProjectState(chosen);
    },
    [api],
  );

  useEffect(() => {
    const controller = new AbortController();
    setPhase("connecting");
    (async () => {
      try {
        const info = await api.health(controller.signal);
        setHealth(info);
        if (info.auth_required && !tokenRef.current) {
          setPhase("needs-token");
          return;
        }
        await settleProject(controller.signal); // a protected call: is the remembered token still good?
        if (controller.signal.aborted) return;
        setPhase("ready");
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && error.unauthorized) return; // onUnauthorized has already asked for a token
        setProblem(describeError(error));
        setPhase("unreachable");
      }
    })();
    return () => controller.abort();
  }, [api, attempt, settleProject]);

  const signIn = useCallback(
    async (token: string): Promise<boolean> => {
      const trimmed = token.trim();
      if (!trimmed) {
        setProblem("Enter the API token.");
        return false;
      }
      tokenRef.current = trimmed;
      try {
        await settleProject();
        writeStored("session", TOKEN_KEY, trimmed);
        setProblem(null);
        setPhase("ready");
        return true;
      } catch (error) {
        dropToken();
        setProblem(error instanceof ApiError && error.unauthorized ? "The server did not accept that token." : describeError(error));
        return false;
      }
    },
    [dropToken, settleProject],
  );

  const signOut = useCallback(() => {
    dropToken();
    setProblem(null);
    setPhase("needs-token");
  }, [dropToken]);

  const setProject = useCallback((name: string) => {
    projectRef.current = name;
    setProjectState(name);
    writeStored("local", PROJECT_KEY, name);
  }, []);

  const value = useMemo<Session>(
    () => ({
      api,
      client,
      phase,
      health,
      problem,
      signIn,
      signOut,
      retry: () => setAttempt((n) => n + 1),
      authRequired: health?.auth_required ?? false,
      project,
      setProject,
    }),
    [api, client, phase, health, problem, signIn, signOut, project, setProject],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
