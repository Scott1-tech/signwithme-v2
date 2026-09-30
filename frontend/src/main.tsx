import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { AuthProvider } from './hooks/useAuth';
import './styles/index.css';
// The embedded PDF faces (Liberation), under their API names: every preview
// of a run or a date is drawn in the face the PDF prints it in.
import './styles/fonts.css';
// After the base sheet on purpose: the scroll viewer overrides a handful of
// the older single-page rules (`.pdf-stage`, `.pdf-page`, `.pdf-canvas`).
import './styles/viewer.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
