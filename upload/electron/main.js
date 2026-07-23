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
  return new Promise((resolve, reject) => {
    const args = ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(backendPort)];
    backendProcess = spawn(pythonPath, args, {
      cwd: dir,
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONUNBUFFERED: '1' }
    });
    backendProcess.stdout.on('data', d => { });
    backendProcess.stderr.on('data', d => { });
    backendProcess.on('error', err => {
      dialog.showErrorBox('Start Failed', 'Python not found. Please install Python and add to PATH.');
      reject(err);
    });
    backendProcess.on('close', code => {
      if (!isQuitting) {
        dialog.showErrorBox('Backend Error', 'Backend service exited unexpectedly (code: ' + code + ')');
      }
    });
    waitForBackend(backendPort, 30000).then(resolve).catch(reject);
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
  mainWindow.on('close', e => { if (!isQuitting) { e.preventDefault(); mainWindow.hide(); } });
  mainWindow.on('closed', () => { mainWindow = null; });
}

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

// ---- Auto Update ----
autoUpdater.autoDownload = false;
autoUpdater.autoInstallOnAppQuit = true;

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

app.whenReady().then(async () => {
  try {
    await startBackend();
    createTray();
    createWindow();
    autoUpdater.checkForUpdatesAndNotify();
  } catch (err) {
    dialog.showErrorBox('Start Error', err.message);
    app.quit();
  }
});

app.on('window-all-closed', () => { if (isQuitting) { stopBackend(); app.quit(); } });
app.on('before-quit', () => { isQuitting = true; stopBackend(); });
