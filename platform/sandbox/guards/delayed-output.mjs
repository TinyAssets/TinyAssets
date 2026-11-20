// A child whose output remains pending for one event-loop turn.
import { Writable } from "node:stream";

for (const name of ["stdout", "stderr"]) {
  const output = process[name];
  Object.defineProperty(process, name, { value: new Writable({
    write(chunk, encoding, done) {
      setImmediate(() => output.write(chunk, encoding, done));
    },
  }) });
}
