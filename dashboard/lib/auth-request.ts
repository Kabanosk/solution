export async function readAuthBody(request: Request): Promise<Record<string, unknown>> {
  const max = 8192;
  if (Number(request.headers.get("content-length")) > max) throw new Error("Request too large.");
  if (!request.body) throw new Error("Invalid request.");
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > max) { await reader.cancel(); throw new Error("Request too large."); }
      chunks.push(value);
    }
    const data = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("Invalid request.");
    return data;
  } finally { reader.releaseLock(); }
}
