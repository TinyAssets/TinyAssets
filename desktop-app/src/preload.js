// A narrow bridge; main checks the exact app origin, path and top frame.
'use strict';
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('tinyassetsDesktop', {
  openExternal: (url) => ipcRenderer.invoke('tinyassets:open-external', url),
  onAppReturn: (callback) => {
    ipcRenderer.removeAllListeners('tinyassets:app-return');
    ipcRenderer.on('tinyassets:app-return', (_event, url) => callback(url));
    ipcRenderer.send('tinyassets:return-ready');
  },
});
