// The hooks Worker's bindings, as its wrangler.jsonc declares them: its Worker Loader and
// the namespaces of its own two classes.
declare namespace Cloudflare {
  interface Env {
    LOADER: WorkerLoader;
    RUNNER: DurableObjectNamespace<import("../src/runner").HookRunner>;
    CODEMODE: DurableObjectNamespace<import("../src/codemode").CodemodeSandbox>;
  }
}
