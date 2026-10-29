// Node's own FormData and File, which jsdom replaces with its own. Node's fetch can't read a
// jsdom File: newer versions (26 here; CI's 22 is older) send it as an empty part named "blob",
// and can't parse an upload back into one. So a test that uploads a file stubs both globals with
// these, as upload-page.test.tsx does.
import { File } from 'node:buffer'

export const NodeFile = File

export const NodeFormData = (await new Response(new URLSearchParams()).formData())
  .constructor as typeof FormData
