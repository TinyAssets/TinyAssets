/**
 * The runner's hook program (`protocol.ts` `hookProgram`): the commands a run's CLI runs on
 * its events and calls (`HOOK_COMMANDS`), the hook helper (`decide.ts`) and the agent's hooks'
 * dispatcher (`hooks.ts`), as an entry of their own, so each hooked event and decided call
 * starts only what those two use, not the whole runner. The runner's build makes it a
 * CommonJS file (`build/bundle.mjs`), which Node starts without its ES module loader, and
 * builds it into the runner's own bytes, which write it beside the runner at each run's start
 * (`runner.ts` `writeHookProgram`): the digest that names the runner names this program too.
 */
import { decideCommand } from "./decide";
import { SYSTEM } from "./engine";
import { hooksCommand } from "./hooks";
import { COMMAND, EXIT, type HookCommand } from "./protocol";

const HANDLERS: { readonly [C in HookCommand]: (args: readonly string[]) => Promise<number> } = {
  [COMMAND.decide]: (args) => decideCommand(args, process.env, process.stdin, process.stdout,
                                            process.stderr),
  [COMMAND.hooks]: (args) => hooksCommand(args, SYSTEM, process.env, process.stdin,
                                          process.stdout, process.stderr),
};

const [command, ...args] = process.argv.slice(2);
if (command === undefined || !Object.hasOwn(HANDLERS, command)) {
  process.stderr.write(`ta-runner: no hook command ${String(command)}\n`);
  process.exit(EXIT.usage);
}
HANDLERS[command as HookCommand](args).then((code) => process.exit(code), (error: unknown) => {
  process.stderr.write(`ta-runner: ${String(error)}\n`);
  process.exit(1);
});
