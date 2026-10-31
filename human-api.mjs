import {isIP} from 'node:net';

// Node reports IPv4-mapped sockets in this form when the listener is dual-stack.
export function isLoopbackAddress(address) {
  if (typeof address !== 'string') return false;
  if (address.startsWith('::ffff:')) return isLoopbackAddress(address.slice(7));
  return address === '::1' || isIP(address) === 4 && address.startsWith('127.');
}

/**
 * Keep the local/Origin fence and add DSH's signed browser-session admission.
 * Desktop's own protocol bridge removes Origin and supplies its Host cookie.
 * A missing Origin alone, an API Peer, or the UI marker is never authority.
 * This authenticates the browser session, not an individual human gesture.
 */
export function humanApiRejection(ctx, request) {
  const unavailable = {status: 503, error: 'Authenticated browser connection unavailable'};
  try {
    const expected = new URL(ctx.webServer.getOrigin());
    const headers = request.headers;
    if (!['127.0.0.1', 'localhost', '[::1]'].includes(expected.hostname) ||
        !isLoopbackAddress(request.socket?.remoteAddress) ||
        headers.host !== expected.host ||
        headers.origin !== undefined && headers.origin !== expected.origin ||
        headers['sec-fetch-site'] === 'cross-site' ||
        headers['x-dsh-math'] !== 'human-ui-v1' ||
        typeof headers['content-type'] !== 'string' || !/^application\/json(?:\s*;|$)/i.test(headers['content-type'])) {
      return {status: 403, error: 'Local same-origin human UI required'};
    }
    const connection = ctx.get('connection');
    if (!connection?.browserPeer || typeof connection.admit !== 'function') return unavailable;
    // Pass the ORIGINAL headers to Connection. It owns credential verification.
    const admission = connection.admit(request);
    if (!admission || typeof admission !== 'object') return unavailable;
    if ('rejection' in admission) {
      if (![401, 403].includes(admission.rejection)) return unavailable;
      return {status: admission.rejection, error: 'Browser authentication required'};
    }
    if (!admission.peer) return unavailable;
    if (admission.peer !== connection.browserPeer) return {status: 403, error: 'Browser UI identity required'};
    if (admission.peer.signal?.aborted) return {status: 401, error: 'Browser authentication required'};
    return undefined;
  } catch {
    // Old/incompatible/missing services must not turn into an unauthenticated route.
    return unavailable;
  }
}
