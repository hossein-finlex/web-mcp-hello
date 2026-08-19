import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { installWebMcpPolyfill } from './webmcp-polyfill';
import './index.css';

// Install BEFORE React mounts, so the very first useEffect that registers a
// tool already finds navigator.modelContext in place.
installWebMcpPolyfill();

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
