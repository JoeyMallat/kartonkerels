import { Container, getContainer } from "@cloudflare/containers";

interface Env {
  APP: DurableObjectNamespace<KartonkerelsApp>;
  SECRET_KEY: string;
  R2_ACCOUNT_ID: string;
  R2_BUCKET: string;
  R2_ACCESS_KEY_ID: string;
  R2_SECRET_ACCESS_KEY: string;
  R2_PATH?: string;
  PKMNPRICES_API_KEY?: string;
  SLEEP_AFTER?: string;
}

export class KartonkerelsApp extends Container<Env> {
  defaultPort = 8080;
  sleepAfter = "30m";
  pingEndpoint = "localhost/health";

  constructor(ctx: DurableObjectState<{}>, env: Env) {
    super(ctx, env);
    // The durable_object scheduling policy requires the image and size at start();
    // @cloudflare/containers doesn't pass them, so inject them here.
    const raw = ctx.container as any;
    (this as any).container = new Proxy(raw, {
      get(target, prop) {
        if (prop === "start") {
          return (options: Record<string, unknown> = {}) =>
            target.start({ ...options, image: target.images.app, instance: "standard-1" });
        }
        const value = target[prop];
        return typeof value === "function" ? value.bind(target) : value;
      },
    });
    if (env.SLEEP_AFTER) this.sleepAfter = env.SLEEP_AFTER;
    this.envVars = {
      SECRET_KEY: env.SECRET_KEY ?? "",
      R2_ACCOUNT_ID: env.R2_ACCOUNT_ID ?? "",
      R2_BUCKET: env.R2_BUCKET ?? "",
      R2_ACCESS_KEY_ID: env.R2_ACCESS_KEY_ID ?? "",
      R2_SECRET_ACCESS_KEY: env.R2_SECRET_ACCESS_KEY ?? "",
      R2_PATH: env.R2_PATH ?? "kartonkerels",
      PKMNPRICES_API_KEY: env.PKMNPRICES_API_KEY ?? "",
      SECURE_COOKIES: "1",
      TRUST_PROXY: "1",
    };
  }

  override onError(error: unknown) {
    console.error("Kartonkerels container error:", error);
    throw error;
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    // Overwrite (never append) forwarding headers so clients cannot spoof their IP for the login rate limit.
    const headers = new Headers(request.headers);
    headers.set("X-Forwarded-For", request.headers.get("CF-Connecting-IP") ?? "");
    headers.set("X-Forwarded-Proto", "https");
    headers.delete("X-Forwarded-Host");

    // Everyone talks to the same single container, so the whole team shares one database.
    return getContainer(env.APP, "main").fetch(new Request(request, { headers }));
  },
} satisfies ExportedHandler<Env>;
