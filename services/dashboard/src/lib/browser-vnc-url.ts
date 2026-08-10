export function buildBrowserVncUrl(routeUrl: (path: string) => string, sessionToken: string): string {
  return routeUrl(`/b/${sessionToken}/vnc/vnc.html?autoconnect=true&resize=scale&reconnect=true&view_only=false&path=b/${sessionToken}/vnc/websockify`);
}
