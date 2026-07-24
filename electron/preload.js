const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  platform: process.platform,
  isElectron: true,
  selectFolder: () => ipcRenderer.invoke('select-folder'),
  openFolderInExplorer: (folderPath) => ipcRenderer.invoke('open-folder-in-explorer', folderPath),
  checkForUpdate: () => ipcRenderer.invoke('check-for-update'),
  downloadUpdate: () => ipcRenderer.invoke('download-update'),
  installUpdate: () => ipcRenderer.invoke('install-update'),
  relaunchApp: () => ipcRenderer.invoke('relaunch-app'),
  onUpdateStatus: (cb) => {
    ipcRenderer.on('update-status', (_, status) => cb(status));
  },
  onPollCheckUpdate: (cb) => {
    ipcRenderer.on('poll-check-update', () => cb());
  }
});
