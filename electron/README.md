# Nellie desktop (Electron shell)

Thin Electron wrapper around the Karna web UI (`nellie web`).

Runs a local `nellie web` process and loads it in a BrowserWindow.
No IPC, no custom protocol — the renderer hits `http://127.0.0.1:3030`
just like a browser would, so every REST/SSE/WebSocket endpoint
already built works unchanged.

## Run in dev

```
cd electron
npm install
npm run dev
```

`--dev` opens devtools and streams the Python server's stdout/stderr
to the terminal.

## Build installers

Per platform:

```
npm run build:mac     # dmg + zip
npm run build:win     # nsis + portable
npm run build:linux   # AppImage + deb
```

Output lands in `electron/dist/`.

## Requirements on the host

Nellie itself must be installed first (the Electron app shells out
to the `nellie` binary):

```
pip install 'karna[rest]'
```

Override which binary is invoked with `NELLIE_BIN=/path/to/nellie`
and which port/host with `NELLIE_HOST` / `NELLIE_PORT`.

## Graceful shutdown

When the last window closes:

- Linux/macOS: `SIGTERM` to the child, then `SIGKILL` after 3s
- Windows: `taskkill /f /t` on the pid tree (signals aren't portable)

This matters because orphaned `uvicorn` workers hold port 3030 hostage,
which is the #1 failure mode on Windows.

## Goose-parity row #19.
