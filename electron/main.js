/**
 * Nellie desktop shell — spawns `nellie web` as a child process and
 * loads its HTTP surface in a BrowserWindow. Single process. No IPC
 * between renderer and Python — the renderer talks to the local HTTP
 * server the same way a browser would, so all the existing REST/SSE/
 * WebSocket endpoints work unchanged.
 *
 * Graceful shutdown: SIGTERM the child on window-all-closed; wait up
 * to 3 seconds for it to exit; SIGKILL if it hangs. This matters on
 * Windows where orphaned uvicorn processes hold port 3030 hostage.
 *
 * Goose-parity row #19.
 */

const { app, BrowserWindow, shell, Menu } = require('electron');
const { spawn } = require('child_process');
const http = require('http');
const path = require('path');

const HOST = process.env.NELLIE_HOST || '127.0.0.1';
const PORT = parseInt(process.env.NELLIE_PORT || '3030', 10);
const NELLIE_BIN = process.env.NELLIE_BIN || 'nellie';
const START_TIMEOUT_MS = 20_000;
const DEV = process.argv.includes('--dev');

let pythonProcess = null;
let mainWindow = null;

function waitForPort(host, port, timeoutMs) {
    const start = Date.now();
    return new Promise((resolve, reject) => {
        const tryConnect = () => {
            const req = http.request(
                { host, port, path: '/health', timeout: 1000, method: 'GET' },
                (res) => {
                    res.resume();
                    if (res.statusCode === 200) return resolve();
                    retry();
                }
            );
            req.on('error', retry);
            req.on('timeout', () => { req.destroy(); retry(); });
            req.end();
        };
        const retry = () => {
            if (Date.now() - start > timeoutMs) {
                reject(new Error(`Nellie web server did not respond on ${host}:${port} within ${timeoutMs}ms`));
            } else {
                setTimeout(tryConnect, 250);
            }
        };
        tryConnect();
    });
}

function startNellieServer() {
    pythonProcess = spawn(NELLIE_BIN, ['web', '--host', HOST, '--port', String(PORT)], {
        env: { ...process.env, KARNA_DESKTOP: '1' },
        stdio: DEV ? 'inherit' : 'pipe',
        shell: process.platform === 'win32',
    });
    pythonProcess.on('error', (err) => {
        console.error(`Failed to launch '${NELLIE_BIN} web':`, err.message);
        console.error('Install Nellie first: pip install karna[rest]');
        app.quit();
    });
    pythonProcess.on('exit', (code, signal) => {
        if (code !== null && code !== 0) {
            console.error(`Nellie server exited with code ${code}`);
        }
        pythonProcess = null;
    });
}

function stopNellieServer() {
    if (!pythonProcess || pythonProcess.exitCode !== null) return;
    try {
        if (process.platform === 'win32') {
            // Windows: graceful signals don't exist; taskkill /T hits the tree
            spawn('taskkill', ['/pid', String(pythonProcess.pid), '/f', '/t']);
        } else {
            pythonProcess.kill('SIGTERM');
        }
    } catch (e) {
        // fall through to SIGKILL below
    }
    // Hard-kill after 3s if still alive
    setTimeout(() => {
        if (pythonProcess && pythonProcess.exitCode === null) {
            try { pythonProcess.kill('SIGKILL'); } catch (_) {}
        }
    }, 3000);
}

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 1400,
        height: 900,
        minWidth: 720,
        minHeight: 480,
        backgroundColor: '#0E0F12',
        show: false,
        autoHideMenuBar: !DEV,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: true,
        },
        title: 'Nellie',
    });

    // Any http/https click from the loaded page opens in the default browser
    // rather than hijacking the app window.
    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
        if (url.startsWith(`http://${HOST}:${PORT}`)) return { action: 'allow' };
        shell.openExternal(url);
        return { action: 'deny' };
    });

    mainWindow.once('ready-to-show', () => mainWindow.show());
    if (DEV) mainWindow.webContents.openDevTools({ mode: 'detach' });

    return mainWindow;
}

app.whenReady().then(async () => {
    // Single-instance lock so multiple clicks don't each spawn their
    // own uvicorn on the same port.
    if (!app.requestSingleInstanceLock()) { app.quit(); return; }
    app.on('second-instance', () => {
        if (mainWindow) {
            if (mainWindow.isMinimized()) mainWindow.restore();
            mainWindow.focus();
        }
    });

    startNellieServer();
    const win = createWindow();
    try {
        await waitForPort(HOST, PORT, START_TIMEOUT_MS);
        await win.loadURL(`http://${HOST}:${PORT}/`);
    } catch (err) {
        console.error(err.message);
        await win.loadURL(
            'data:text/html,' +
                encodeURIComponent(
                    `<html><body style="background:#0E0F12;color:#E6E8EC;font:14px system-ui;padding:40px">` +
                        `<h2>Could not start Nellie</h2><pre>${err.message}</pre>` +
                        `<p>Install: <code>pip install 'karna[rest]'</code></p>` +
                        `</body></html>`
                )
        );
    }

    // macOS: re-create the window when the dock icon is clicked and
    // no windows are open.
    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
});

app.on('window-all-closed', () => {
    stopNellieServer();
    if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', stopNellieServer);
