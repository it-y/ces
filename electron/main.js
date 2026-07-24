const { app, BrowserWindow, shell, dialog, Tray, Menu, nativeImage, ipcMain } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const kill = require('tree-kill');
const net = require('net');
const fs = require('fs');
const { autoUpdater } = require('electron-updater');

let mainWindow = null;
let tray = null;
let backendProcess = null;
let isQuitting = false;
let backendPort = 3000;
let logStream = null;
let updatePollTimer = null;

// ========== 单实例锁 ==========
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }
  });
}

function getLogPath() {
  const dir = getProjectDir();
  return path.join(dir, 'data', 'logs', 'backend.log');
}

function appendLog(msg) {
  try {
    if (!logStream) {
      const p = getLogPath();
      fs.mkdirSync(path.dirname(p), { recursive: true });
      logStream = fs.createWriteStream(p, { flags: 'a', encoding: 'utf-8' });
    }
    logStream.write('[' + new Date().toISOString() + '] ' + msg + '\n');
  } catch(e) {}
}

function getProjectDir() {
  if (app.isPackaged) {
    return process.resourcesPath;
  }
  return path.join(__dirname, '..');
}

function getPythonPath() {
  const dir = getProjectDir();
  const embedded = path.join(dir, 'python', 'python.exe');
  if (fs.existsSync(embedded)) {
    return embedded;
  }
  return 'python';
}

function getIconPath() {
  const dir = getProjectDir();
  const icoPath = path.join(dir, 'static', 'images', 'logo-256.png');
  if (fs.existsSync(icoPath)) return icoPath;
  return path.join(dir, 'static', 'images', 'logo.png');
}

function installDependencies(pythonPath) {
  const dir = getProjectDir();
  const requirementsPath = path.join(dir, 'requirements.txt');
  if (!fs.existsSync(requirementsPath)) {
    return Promise.resolve();
  }
  const markerFile = path.join(dir, '.deps_installed');
  if (fs.existsSync(markerFile)) {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const args = ['-m', 'pip', 'install', '-r', requirementsPath, '--quiet'];
    const pip = spawn(pythonPath, args, { cwd: dir, stdio: ['pipe', 'pipe', 'pipe'] });
    let stderr = '';
    pip.stderr.on('data', d => { stderr += d.toString(); });
    pip.stdout.on('data', d => { });
    pip.on('close', code => {
      if (code === 0) {
        fs.writeFileSync(markerFile, new Date().toISOString());
        resolve();
      } else {
        reject(new Error('pip install failed:\n' + stderr));
      }
    });
    pip.on('error', err => { reject(err); });
  });
}

function waitForBackend(port, timeout) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const check = () => {
      const s = new net.Socket();
      s.setTimeout(1000);
      s.once('connect', () => { s.destroy(); resolve(); });
      s.once('timeout', () => { s.destroy(); retry(); });
      s.once('error', () => { s.destroy(); retry(); });
      function retry() {
        if (Date.now() - start > timeout) return reject(new Error('Backend start timeout'));
        setTimeout(check, 500);
      }
      s.connect(port, '127.0.0.1');
    };
    check();
  });
}

function probePython(pythonPath) {
  return new Promise((resolve) => {
    const p = spawn(pythonPath, ['-c', 'import uvicorn; print("ok")'], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONUNBUFFERED: '1' }
    });
    let out = '';
    p.stdout.on('data', d => out += d);
    p.stderr.on('data', d => out += d);
    p.on('close', code => resolve(code === 0));
  });
}

function findFreePort(startPort) {
  return new Promise((resolve, reject) => {
    const tryPort = (port) => {
      if (port > startPort + 100) return reject(new Error('No free port found'));
      const s = new net.Server();
      s.listen(port, '127.0.0.1', () => { s.close(() => resolve(port)); });
      s.on('error', () => { try { s.destroy(); } catch(e) {} tryPort(port + 1); });
    };
    tryPort(startPort);
  });
}

async function startBackend() {
  const pythonPath = getPythonPath();
  const dir = getProjectDir();
  try {
    await installDependencies(pythonPath);
  } catch (err) {
    const skip = dialog.showMessageBoxSync({
      type: 'warning',
      title: 'Dependency Install Failed',
      message: 'Dependency installation failed. The app may not work correctly.\n\n' + err.message + '\n\nContinue?',
      buttons: ['Continue', 'Exit'],
      defaultId: 1
    });
    if (skip === 1) { app.quit(); return; }
  }
  const pythonOk = await probePython(pythonPath);
  if (!pythonOk) {
    appendLog('Python probe failed');
    dialog.showErrorBox('Python Error',
      'Python environment is not working.\n' +
      'Python path: ' + pythonPath + '\n\n' +
      'The embedded Python may be corrupted. Try reinstalling the app.');
    app.quit();
    return;
  }
  appendLog('Python probe OK');
  backendPort = await findFreePort(backendPort);
  appendLog('Using port: ' + backendPort);
  return new Promise((resolve, reject) => {
    const args = ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(backendPort)];
    appendLog('Starting: ' + pythonPath + ' ' + args.join(' ') + ' (cwd=' + dir + ')');
    backendProcess = spawn(pythonPath, args, {
      cwd: dir,
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONUNBUFFERED: '1', ELECTRON_RUN: '1' }
    });
    let stderrBuf = '';
    backendProcess.stdout.on('data', d => { appendLog('[py] ' + d.toString().trimEnd()); });
    backendProcess.stderr.on('data', d => {
      const text = d.toString();
      stderrBuf += text;
      appendLog('[py-err] ' + text.trimEnd());
    });
    backendProcess.on('error', err => {
      appendLog('Spawn error: ' + err.message);
      dialog.showErrorBox('Start Failed', 'Python not found. Please install Python and add to PATH.\n\n' + err.message);
      reject(err);
    });
    backendProcess.on('close', code => {
      appendLog('Process exited with code: ' + code);
      if (!isQuitting && !resolved) {
        const logPath = getLogPath();
        dialog.showErrorBox('Backend Error',
          'Backend service exited unexpectedly (code: ' + code + ').\n\n' +
          'Last log output:\n' + (stderrBuf.slice(-500) || '(no output)') + '\n\n' +
          'Full log: ' + logPath);
      }
    });
    let resolved = false;
    const origResolve = resolve;
    resolve = function(v) { resolved = true; origResolve(v); };
    waitForBackend(backendPort, 30000).then(resolve).catch(function(err) {
      if (!isQuitting) {
        appendLog('Backend timeout: ' + err.message);
        dialog.showErrorBox('Start Error',
          'Backend did not start within 30 seconds.\n\n' +
          'Last log output:\n' + (stderrBuf.slice(-500) || '(no output)') + '\n\n' +
          'Full log: ' + getLogPath());
        reject(err);
      }
    });
  });
}

function stopBackend() {
  if (backendProcess) {
    try { kill(backendProcess.pid); } catch(e) {}
    backendProcess = null;
  }
}

function createTray() {
  const icon = nativeImage.createFromPath(getIconPath());
  tray = new Tray(icon.isEmpty() ? nativeImage.createEmpty() : icon.resize({ width: 16, height: 16 }));
  tray.setToolTip('Infinite Canvas');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Show', click: () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } } },
    { type: 'separator' },
    { label: 'Exit', click: () => { isQuitting = true; stopBackend(); app.quit(); } }
  ]));
  tray.on('double-click', () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 800,
    minHeight: 600,
    title: 'Infinite Canvas',
    icon: getIconPath(),
    backgroundColor: '#0f141d',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });
  mainWindow.loadURL('http://127.0.0.1:' + backendPort);
  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: 'deny' }; });
  // X 按钮 → 隐藏到托盘（不退出）
  mainWindow.on('close', e => { if (!isQuitting) { e.preventDefault(); mainWindow.hide(); } });
  mainWindow.on('closed', () => { mainWindow = null; });
  // 定时检测更新（每 60 秒通知前端刷新版本状态）
  startUpdatePoll();
}

function startUpdatePoll() {
  if (updatePollTimer) clearInterval(updatePollTimer);
  updatePollTimer = setInterval(() => {
    if (mainWindow) {
      mainWindow.webContents.send('poll-check-update');
    }
  }, 60000);
}

// ========== IPC ==========
ipcMain.handle('select-folder', async () => {
  const result = await dialog.showOpenDialog({
    properties: ['openDirectory'],
    title: '选择要打开的文件夹'
  });
  if (result.canceled || !result.filePaths.length) return null;
  return result.filePaths[0];
});

ipcMain.handle('open-folder-in-explorer', async (event, folderPath) => {
  if (!folderPath) return;
  const { exec } = require('child_process');
  exec(`explorer "${folderPath}"`);
});

// ========== Electron Auto Update ==========
// 默认静默，不自动检查（没配 GitHub Releases）
autoUpdater.autoDownload = false;
autoUpdater.autoInstallOnAppQuit = false;

function sendUpdateStatus(status) {
  if (mainWindow) mainWindow.webContents.send('update-status', status);
}

autoUpdater.on('checking-for-update', () => {
  sendUpdateStatus({ type: 'checking' });
});

autoUpdater.on('update-available', (info) => {
  sendUpdateStatus({ type: 'available', version: info.version, releaseDate: info.releaseDate });
});

autoUpdater.on('update-not-available', () => {
  sendUpdateStatus({ type: 'not-available' });
});

autoUpdater.on('download-progress', (progress) => {
  sendUpdateStatus({ type: 'downloading', percent: Math.round(progress.percent) });
});

autoUpdater.on('update-downloaded', (info) => {
  sendUpdateStatus({ type: 'downloaded', version: info.version });
});

autoUpdater.on('error', (err) => {
  sendUpdateStatus({ type: 'error', message: err ? (err.message || err.stack || String(err)) : 'Unknown error' });
});

ipcMain.handle('check-for-update', async () => {
  try {
    autoUpdater.checkForUpdates();
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err.message };
  }
});

ipcMain.handle('download-update', async () => {
  try {
    autoUpdater.downloadUpdate();
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err.message };
  }
});

ipcMain.handle('install-update', async () => {
  setImmediate(() => autoUpdater.quitAndInstall());
  return { ok: true };
});

ipcMain.handle('relaunch-app', async () => {
  appendLog('Relaunching app from update');
  isQuitting = true;
  stopBackend();
  app.relaunch();
  app.exit(0);
});

app.whenReady().then(async () => {
  try {
    await startBackend();
    createTray();
    createWindow();
  } catch (err) {
    dialog.showErrorBox('Start Error', err.message);
    app.quit();
  }
});

app.on('window-all-closed', () => { if (isQuitting) { stopBackend(); app.quit(); } });
app.on('before-quit', () => { isQuitting = true; stopBackend(); });
