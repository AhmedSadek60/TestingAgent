import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiClient } from "./api/client";
import { createApi, type Api } from "./api/endpoints";
import { ApiError, describeError } from "./api/errors";
import type { AuthOptions, Health, Me, RegisterRequest } from "./api/types";
import { PROJECT_KEY, readStored, TOKEN_KEY, writeStored } from "./lib/storage";

export type AuthPhase = "connecting" | "unreachable" | "needs-token" | "ready";

export interface Session {
  api: Api;
  client: ApiClient;
  phase: AuthPhase;
  health: Health | null;
  /** Why the last sign-in or the connection failed. */
  problem: string | null;
  /** The API token (the shared service credential). */
  signIn: (token: string) => Promise<boolean>;
  /** A user name and password. */
  signInPassword: (username: string, password: string) => Promise<boolean>;
  /** Make an account. Resolves to what happened: signed in, waiting for an administrator, or refused (see `problem`). */
  register: (body: RegisterRequest) => Promise<"signed-in" | "pending" | "refused">;
  signOut: () => void;
  /** Who the server believes this is, once known: a person, the shared token or the local machine. */
  me: Me | null;
  /** How this server lets people sign in, when it says (an older server does not). */
  options: AuthOptions | null;
  /** A message that is not a problem: an account waiting for approval. */
  notice: string | null;
  refreshMe: () => Promise<void>;
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
  const [me, setMe] = useState<Me | null>(null);
  const [options, setOptions] = useState<AuthOptions | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const csrfRef = useRef<string | null>(null);
  const sessionRef = useRef(false); // true while the cookie session (not the token) is what authenticates this page
  const [project, setProjectState] = useState<string>(readStored("local", PROJECT_KEY) ?? "default");
  const projectRef = useRef(project);
  const [attempt, setAttempt] = useState(0);

  const dropToken = useCallback(() => {
    tokenRef.current = null;
    writeStored("session", TOKEN_KEY, null);
  }, []);

  const forget = useCallback(() => {
    dropToken();
    csrfRef.current = null;
    sessionRef.current = false;
    setMe(null);
  }, [dropToken]);

  const client = useMemo(
    () =>
      new ApiClient({
        base,
        getToken: () => tokenRef.current,
        getCsrf: () => csrfRef.current,
        onUnauthorized: () => {
          const bySession = sessionRef.current && !tokenRef.current;
          forget();
          setProblem(bySession ? "Your session has ended. Sign in again." : "The server did not accept the API token. Enter it again.");
          setPhase((current) => (current === "ready" || current === "connecting" ? "needs-token" : current));
        },
      }),
    [base, forget],
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

  /** Who is this? Best effort: an older server has no such call, and a person who has not signed in gets a 401. */
  const learnMe = useCallback(
    async (signal?: AbortSignal): Promise<Me | null> => {
      try {
        const who = await api.me(signal);
        csrfRef.current = who.csrf_token ?? null;
        sessionRef.current = who.kind === "user";
        setMe(who);
        return who;
      } catch (error) {
        if (signal?.aborted || (error instanceof DOMException && error.name === "AbortError")) throw error;
        return null;
      }
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
        setOptions(await api.authOptions(controller.signal).catch(() => null));
        if (!tokenRef.current) {
          // a cookie session from an earlier visit, or a machine that needs no token
          const who = await learnMe(controller.signal);
          if (who) {
            if (who.operator) await settleProject(controller.signal);
            if (controller.signal.aborted) return;
            setPhase("ready");
            return;
          }
          if (info.auth_required) {
            setPhase("needs-token");
            return;
          }
        }
        await settleProject(controller.signal); // a protected call: is the remembered token still good?
        if (controller.signal.aborted) return;
        if (tokenRef.current) await learnMe(controller.signal);
        setPhase("ready");
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && error.unauthorized) return; // onUnauthorized has already asked for a token
        setProblem(describeError(error));
        setPhase("unreachable");
      }
    })();
    return () => controller.abort();
  }, [api, attempt, settleProject, learnMe]);

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
        setNotice(null);
        await learnMe();
        setPhase("ready");
        return true;
      } catch (error) {
        dropToken();
        setProblem(error instanceof ApiError && error.unauthorized ? "The server did not accept that token." : describeError(error));
        return false;
      }
    },
    [dropToken, settleProject, learnMe],
  );

  const enter = useCallback(
    async (who: Me): Promise<void> => {
      csrfRef.current = who.csrf_token ?? null;
      sessionRef.current = who.kind === "user";
      setMe(who);
      if (who.operator) await settleProject();
      setProblem(null);
      setNotice(null);
      setPhase("ready");
    },
    [settleProject],
  );

  const signInPassword = useCallback(
    async (username: string, password: string): Promise<boolean> => {
      if (!username.trim() || !password) {
        setProblem("Enter your user name and password.");
        return false;
      }
      try {
        const result = await api.login(username.trim(), password);
        await enter(result.me);
        return true;
      } catch (error) {
        setProblem(describeError(error));
        return false;
      }
    },
    [api, enter],
  );

  const register = useCallback(
    async (body: RegisterRequest): Promise<"signed-in" | "pending" | "refused"> => {
      try {
        const result = await api.register(body);
        if (result.signed_in && result.me) {
          await enter(result.me);
          return "signed-in";
        }
        setProblem(null);
        setNotice("Your account was created. An administrator has to approve it before you can sign in.");
        return "pending";
      } catch (error) {
        setProblem(describeError(error));
        return "refused";
      }
    },
    [api, enter],
  );

  const signOut = useCallback(() => {
    if (sessionRef.current && !tokenRef.current) void api.logout().catch(() => undefined); // end it on the server too
    forget();
    setProblem(null);
    setNotice(null);
    setPhase("needs-token");
  }, [api, forget]);

  const refreshMe = useCallback(async () => {
    await learnMe();
  }, [learnMe]);

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
      signInPassword,
      register,
      signOut,
      me,
      options,
      notice,
      refreshMe,
      retry: () => setAttempt((n) => n + 1),
      authRequired: health?.auth_required ?? false,
      project,
      setProject,
    }),
    [api, client, phase, health, problem, signIn, signInPassword, register, signOut, me, options, notice, refreshMe, project, setProject],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
