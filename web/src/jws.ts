/** Browser-side check of a compact ES256 JWS against published keys (WebCrypto). Used to show that an answer or status list was really signed. */
const dec = new TextDecoder();
const b64u = (s: string) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(s.length / 4) * 4, "=")), (c) => c.charCodeAt(0));

export const jwsPayload = (t: string): any => JSON.parse(dec.decode(b64u(t.split(".")[1])));

export async function verifyJws(token: string, jwks: { keys: any[] }): Promise<{ ok: boolean; reason?: string; payload?: any; expired?: boolean }> {
  try {
    const [h, p, sg] = token.split(".");
    const header = JSON.parse(dec.decode(b64u(h)));
    const k = jwks.keys.find((x) => x.kid === header.kid);
    if (!k) return { ok: false, reason: "The signing key is not in the published key set." };
    const key = await crypto.subtle.importKey("jwk", { kty: k.kty, crv: k.crv, x: k.x, y: k.y, ext: true }, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
    const ok = await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, b64u(sg), new TextEncoder().encode(`${h}.${p}`));
    const payload = JSON.parse(dec.decode(b64u(p)));
    return { ok, payload, expired: typeof payload.exp === "number" && payload.exp * 1000 < Date.now(), reason: ok ? undefined : "The signature does not match." };
  } catch (e) {
    return { ok: false, reason: "Could not check the signature in this browser." };
  }
}
