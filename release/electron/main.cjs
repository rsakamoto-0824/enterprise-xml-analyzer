const { app, BrowserWindow, protocol, net, session } = require('electron');
const path = require('path');
const fs = require('fs');
const { pathToFileURL } = require('url');
protocol.registerSchemesAsPrivileged([{scheme:'xmlapp',privileges:{standard:true,secure:true,supportFetchAPI:true,stream:true}}]);

const WINDOW_WIDTH_PX = 1280;
const WINDOW_HEIGHT_PX = 800;

function findBuiltIndexHtml() {
  const candidates = [
    path.join(__dirname, '..', '..', 'dist', 'index.html'),
    path.join(__dirname, '..', '..', 'out', 'index.html'),
    path.join(__dirname, '..', '..', 'build', 'index.html')
  ];

  return candidates.find((candidatePath) => fs.existsSync(candidatePath));
}

function createWindow() {
  const window = new BrowserWindow({
    width: WINDOW_WIDTH_PX,
    height: WINDOW_HEIGHT_PX,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  });

  const builtIndexHtml = findBuiltIndexHtml();

  if (!builtIndexHtml) {
    throw new Error('Built index.html was not found. Run the web build first.');
  }

  window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  window.webContents.on('will-navigate',(event,url)=>{if(!url.startsWith('xmlapp://app/'))event.preventDefault();});
  window.loadURL('xmlapp://app/');
}

app.whenReady().then(()=>{
  const root=path.resolve(__dirname,'../../dist');
  protocol.handle('xmlapp',request=>{
    const url=new URL(request.url);
    const pathname=decodeURIComponent(url.pathname==='/'?'/index.html':url.pathname);
    const file=path.resolve(root,'.'+pathname);
    if(url.hostname!=='app'||!file.startsWith(root+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile())return new Response('Not found',{status:404});
    return net.fetch(pathToFileURL(file).href);
  });
  session.defaultSession.webRequest.onBeforeRequest((details,callback)=>callback({cancel:!details.url.startsWith('xmlapp://app/')&&!details.url.startsWith('file:')&&!details.url.startsWith('blob:')&&!details.url.startsWith('data:')}));
  session.defaultSession.webRequest.onHeadersReceived((details,callback)=>callback({responseHeaders:{...details.responseHeaders,'Content-Security-Policy':["default-src 'none'; script-src 'self'; worker-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'none'; object-src 'none'; base-uri 'self'; form-action 'none'"]}}));
  createWindow();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});
