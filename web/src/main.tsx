import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { AppProvider } from './store';
import { CssBaseline, ThemeProvider } from '@mui/material';
import { ResearchProvider } from './research/context';
import { theme } from './theme';
import '@fontsource/roboto/latin-400.css';
import '@fontsource/roboto/latin-500.css';
import '@fontsource/roboto/latin-700.css';
import './styles.css';
import './research.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider theme={theme}><CssBaseline /><AppProvider><ResearchProvider><App /></ResearchProvider></AppProvider></ThemeProvider>
  </StrictMode>,
);
