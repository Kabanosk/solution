import { isIP } from "node:net";

/** Trust only a deployment-configured header that the ingress OVERWRITES, never an arbitrary forwarded value. */
export function clientAddress(headers: Headers, header = process.env.SCOUT_TRUSTED_CLIENT_IP_HEADER, production = process.env.NODE_ENV === "production") {
  if (!header) {
    if (production) throw new Error("Trusted client address is not configured");
    return "local-development";
  }
  const address = headers.get(header)?.trim() ?? "";
  if (!isIP(address)) throw new Error("Invalid trusted client address");
  return address;
}
