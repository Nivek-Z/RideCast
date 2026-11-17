import { createTheme } from '@mui/material/styles';

// MD3 color, type and shape roles; MUI's default MD2 styling is overridden here.
export const md3 = {
  primary: '#405f90', onPrimary: '#ffffff', primaryContainer: '#d9e6ff', onPrimaryContainer: '#17385f',
  secondary: '#565f71', secondaryContainer: '#dce3f1', onSecondaryContainer: '#182131',
  surface: '#f8f9ff', surfaceContainerLow: '#f2f3f9', surfaceContainer: '#eceef5',
  surfaceContainerHigh: '#e6e8ef', surfaceContainerLowest: '#ffffff',
  onSurface: '#191c22', onSurfaceVariant: '#535b69', outline: '#747b89', outlineVariant: '#d5d9e3',
  error: '#a33b32', errorContainer: '#ffdad5', tertiary: '#4c635b', tertiaryContainer: '#d8eade',
};

export const theme = createTheme({
  palette: {
    mode: 'light', primary: { main: md3.primary, contrastText: md3.onPrimary },
    secondary: { main: md3.secondary }, background: { default: md3.surface, paper: md3.surfaceContainerLowest },
    text: { primary: md3.onSurface, secondary: md3.onSurfaceVariant }, divider: md3.outlineVariant,
    error: { main: md3.error }, success: { main: md3.tertiary },
  },
  shape: { borderRadius: 12 },
  typography: {
    fontFamily: 'Roboto, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
    h1: { fontSize: 28, lineHeight: '36px', fontWeight: 400, letterSpacing: '-.3px' },
    h2: { fontSize: 22, lineHeight: '28px', fontWeight: 400 },
    h3: { fontSize: 18, lineHeight: '26px', fontWeight: 500 },
    subtitle1: { fontSize: 16, lineHeight: '24px', fontWeight: 500 },
    body1: { fontSize: 14, lineHeight: '22px' }, body2: { fontSize: 13, lineHeight: '20px' },
    caption: { fontSize: 12, lineHeight: '18px', letterSpacing: '.1px' },
    button: { fontSize: 14, lineHeight: '20px', fontWeight: 500, textTransform: 'none', letterSpacing: 0 },
  },
  components: {
    MuiButton: { defaultProps: { disableElevation: true }, styleOverrides: { root: { borderRadius: 24, minHeight: 40, padding: '8px 20px' }, outlined: { borderColor: md3.outlineVariant } } },
    MuiIconButton: { styleOverrides: { root: { color: md3.onSurfaceVariant, width: 40, height: 40 } } },
    MuiChip: { styleOverrides: { root: { borderRadius: 8, height: 28, fontSize: 12 }, filled: { backgroundColor: md3.surfaceContainer } } },
    MuiPaper: { defaultProps: { elevation: 0 }, styleOverrides: { root: { backgroundImage: 'none' }, rounded: { borderRadius: 16 } } },
    MuiDialog: { styleOverrides: { paper: { borderRadius: 28, backgroundColor: md3.surfaceContainerLow, padding: 4 } } },
    MuiDrawer: { styleOverrides: { paper: { backgroundColor: md3.surface, width: 440, maxWidth: '100vw', borderRadius: '28px 0 0 28px', padding: 28 } } },
    MuiOutlinedInput: { styleOverrides: { root: { borderRadius: 8, fontSize: 14, minHeight: 44 }, notchedOutline: { borderColor: md3.outlineVariant } } },
    MuiInputLabel: { styleOverrides: { root: { fontSize: 14 } } },
    MuiTabs: { styleOverrides: { root: { minHeight: 48 }, indicator: { height: 3, borderRadius: '3px 3px 0 0' } } },
    MuiTab: { styleOverrides: { root: { minHeight: 48, textTransform: 'none', fontSize: 14, padding: '12px 20px' } } },
    MuiToggleButton: { styleOverrides: { root: { textTransform: 'none', fontSize: 13, padding: '8px 16px', borderColor: md3.outlineVariant, '&.Mui-selected': { backgroundColor: md3.secondaryContainer, color: md3.onSecondaryContainer }, '&.Mui-selected:hover': { backgroundColor: '#d2dced' } } } },
    MuiTableCell: { styleOverrides: { root: { borderBottom: `1px solid ${md3.outlineVariant}`, fontSize: 13, padding: '16px 20px' }, head: { fontSize: 12, color: md3.onSurfaceVariant, background: md3.surfaceContainerLow, fontWeight: 500 } } },
    MuiCheckbox: { styleOverrides: { root: { padding: 10, color: md3.outline } } },
    MuiMenu: { styleOverrides: { paper: { borderRadius: 12, boxShadow: '0 6px 24px #273d5c18', backgroundColor: md3.surfaceContainerLow } } },
    MuiMenuItem: { styleOverrides: { root: { minHeight: 48, fontSize: 14 } } },
    MuiTooltip: { styleOverrides: { tooltip: { borderRadius: 8, fontSize: 12, padding: '8px 12px', backgroundColor: '#2d313b' } } },
  },
});
