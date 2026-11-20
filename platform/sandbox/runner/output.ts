/** A runner or probe command may exit once stdout has finished writing its reply. */
export function writeAnswer(text: string, code: number): Promise<number> {
  return new Promise((resolve, reject) => {
    process.stdout.write(text, (error) => error ? reject(error) : resolve(code));
  });
}
