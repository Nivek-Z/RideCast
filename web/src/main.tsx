import { StrictMode, useEffect, useLayoutEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { CssBaseline, GlobalStyles, ThemeProvider, createTheme } from '@mui/material';
import { App } from './App';
import {
  differenceTones, initialModeOverride, initialPalette, modeStorageKey, palettes, paletteStorageKey,
  savePreference, type ColorMode, type PaletteId,
} from './palettes';
import '@fontsource/roboto/latin-400.css';
import '@fontsource/roboto/latin-500.css';
import '@fontsource/roboto/latin-700.css';
import './study.css';

const darkQuery = '(prefers-color-scheme: dark)';

function RideCast() {
  const [id, setId] = useState(initialPalette);
  const [modeOverride, setModeOverride] = useState(initialModeOverride);
  const [systemMode, setSystemMode] = useState<ColorMode>(() => window.matchMedia(darkQuery).matches ? 'dark' : 'light');
  const mode = modeOverride ?? systemMode;
  const { colors, chartTones } = palettes[id][mode];
  const panel = colors[mode === 'dark' ? 'surface-container-low' : 'surface-container-lowest'];
  const theme = useMemo(() => createTheme({
    palette: {
      mode,
      primary: { main: colors.primary, contrastText: colors['on-primary'] },
      secondary: { main: colors.secondary },
      text: { primary: colors['on-surface'], secondary: colors['on-surface-variant'] },
      background: { default: colors.surface, paper: panel },
      divider: colors['outline-variant'],
      error: { main: colors.error },
    },
    shape: { borderRadius: 12 },
    typography: {
      fontFamily: 'Roboto, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
      button: { textTransform: 'none', fontWeight: 500 },
    },
    components: {
      MuiButton: {
        defaultProps: { disableElevation: true },
        styleOverrides: { root: { borderRadius: 100, minHeight: 40, paddingInline: 17 } },
      },
      MuiMenu: { styleOverrides: { paper: { backgroundColor: colors['surface-container'], backgroundImage: 'none' } } },
      MuiSelect: { styleOverrides: { select: { fontSize: 14 } } },
    },
  }), [colors, mode, panel]);
  const tokens = useMemo(() => Object.fromEntries([
    ...Object.entries(colors).map(([role, color]) => [`--md-sys-color-${role}`, color]),
    ...chartTones.map((color, index) => [`--chart-tone-${index + 1}`, color]),
    ...differenceTones[mode].under.map((color, index) => [`--under-tone-${index + 1}`, color]),
    ...differenceTones[mode].over.map((color, index) => [`--over-tone-${index + 1}`, color]),
    ['--panel-surface', panel],
  ]), [colors, chartTones, mode, panel]);

  useEffect(() => {
    if (modeOverride) return;
    const media = window.matchMedia(darkQuery);
    const updateMode = () => setSystemMode(media.matches ? 'dark' : 'light');
    updateMode();
    media.addEventListener('change', updateMode);
    return () => media.removeEventListener('change', updateMode);
  }, [modeOverride]);

  useLayoutEffect(() => {
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', colors.surface);
  }, [colors]);

  function selectPalette(value: PaletteId) {
    setId(value);
    savePreference(paletteStorageKey, value);
  }

  function toggleMode() {
    const next = mode === 'dark' ? 'light' : 'dark';
    setModeOverride(next);
    savePreference(modeStorageKey, next);
  }

  return <ThemeProvider theme={theme}>
    <CssBaseline enableColorScheme />
    <GlobalStyles styles={{ ':root': tokens }} />
    <App palette={id} selectPalette={selectPalette} mode={mode} toggleMode={toggleMode} />
  </ThemeProvider>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><RideCast /></StrictMode>);
