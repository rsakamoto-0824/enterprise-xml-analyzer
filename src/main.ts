import './styles/main.css';
import { App } from './app/App';
void new App().start().catch(() => {
  const root = document.querySelector('#app');
  if (root) {
    const p = document.createElement('p');
    p.textContent = '起動できませんでした。ブラウザの保存領域・HTTPS接続を確認してください。';
    root.append(p);
  }
});
