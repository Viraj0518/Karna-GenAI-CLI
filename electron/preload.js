/**
 * Intentionally minimal preload. The renderer talks to the local
 * nellie web server over HTTP/WebSocket same as a browser would.
 * We expose only a single flag so the web UI can optionally show
 * "native" affordances (e.g. hide the "Install Karna desktop app"
 * banner) when running inside Electron.
 */

const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('nellie', {
    isDesktop: true,
    platform: process.platform,
});
