// The one thing the tests take from Node's node:buffer (node-uploads.ts). The app's tsconfig
// leaves out Node's types, so they don't leak into the app's code.
declare module 'node:buffer' {
  export const File: typeof globalThis.File
}
