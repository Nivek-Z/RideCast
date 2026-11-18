export type ColorMode = 'light' | 'dark';

interface PaletteTheme {
  colors: Record<string, string>;
  chartTones: readonly [string, string, string, string, string, string];
}

type Palette = { name: string } & Record<ColorMode, PaletteTheme>;

// Pixel uses the Android Material You fallback seed #1b6ef3, rendered in Tonal Spot.
// Violet uses the Material Web baseline (v0.192); jade keeps RideCast's original seed #236653.
export const palettes = {
  pixel: {
    name: 'Pixel 蓝',
    light: {
      colors: {
        primary: '#475d92', 'on-primary': '#ffffff', 'primary-container': '#d9e2ff',
        'on-primary-container': '#2f4578', secondary: '#575e71', 'secondary-container': '#dce2f9',
        'on-secondary-container': '#404659',
        surface: '#faf8ff', 'on-surface': '#1a1b20', 'on-surface-variant': '#44464f',
        'surface-container-lowest': '#ffffff', 'surface-container-low': '#f4f3fa',
        'surface-container': '#eeedf4', 'surface-container-high': '#e8e7ef',
        outline: '#757780', 'outline-variant': '#c5c6d0',
        error: '#ba1a1a', 'error-container': '#ffdad6', 'on-error-container': '#93000a',
      },
      chartTones: ['#eef0ff', '#c5d4ff', '#94aae4', '#6d83ba', '#475d92', '#223a6c'],
    },
    dark: {
      colors: {
        primary: '#b0c6ff', 'on-primary': '#152e60', 'primary-container': '#2f4578',
        'on-primary-container': '#d9e2ff', secondary: '#c0c6dc', 'secondary-container': '#404659',
        'on-secondary-container': '#dce2f9',
        surface: '#121318', 'on-surface': '#e2e2e9', 'on-surface-variant': '#c5c6d0',
        'surface-container-lowest': '#0c0e13', 'surface-container-low': '#1a1b20',
        'surface-container': '#1e1f25', 'surface-container-high': '#282a2f',
        outline: '#8f9099', 'outline-variant': '#44464f',
        error: '#ffb4ab', 'error-container': '#93000a', 'on-error-container': '#ffdad6',
      },
      chartTones: ['#2a4173', '#40568a', '#6378af', '#8298d0', '#a2b8f2', '#d5dfff'],
    },
  },
  violet: {
    name: '经典紫',
    light: {
      colors: {
        primary: '#6750a4', 'on-primary': '#ffffff', 'primary-container': '#eaddff',
        'on-primary-container': '#21005d', secondary: '#625b71', 'secondary-container': '#e8def8',
        'on-secondary-container': '#1d192b',
        surface: '#fef7ff', 'on-surface': '#1d1b20', 'on-surface-variant': '#49454f',
        'surface-container-lowest': '#ffffff', 'surface-container-low': '#f7f2fa',
        'surface-container': '#f3edf7', 'surface-container-high': '#ece6f0',
        outline: '#79747e', 'outline-variant': '#cac4d0',
        error: '#b3261e', 'error-container': '#f9dedc', 'on-error-container': '#410e0b',
      },
      chartTones: ['#f6edff', '#d0bcff', '#b69df8', '#7f67be', '#6750a4', '#4f378b'],
    },
    dark: {
      colors: {
        primary: '#d0bcff', 'on-primary': '#381e72', 'primary-container': '#4f378b',
        'on-primary-container': '#eaddff', secondary: '#ccc2dc', 'secondary-container': '#4a4458',
        'on-secondary-container': '#e8def8',
        surface: '#141218', 'on-surface': '#e6e0e9', 'on-surface-variant': '#cac4d0',
        'surface-container-lowest': '#0f0d13', 'surface-container-low': '#1d1b20',
        'surface-container': '#211f26', 'surface-container-high': '#2b2930',
        outline: '#938f99', 'outline-variant': '#49454f',
        error: '#f2b8b5', 'error-container': '#8c1d18', 'on-error-container': '#f9dedc',
      },
      chartTones: ['#4f378b', '#6750a4', '#7f67be', '#9a82db', '#b69df8', '#eaddff'],
    },
  },
  jade: {
    name: '青绿',
    light: {
      colors: {
        primary: '#146b55', 'on-primary': '#ffffff', 'primary-container': '#a3f2d6',
        'on-primary-container': '#00513f', secondary: '#4b635a', 'secondary-container': '#cee9dd',
        'on-secondary-container': '#344c43',
        surface: '#f5fbf6', 'on-surface': '#171d1a', 'on-surface-variant': '#3f4945',
        'surface-container-lowest': '#ffffff', 'surface-container-low': '#eff5f0',
        'surface-container': '#e9efeb', 'surface-container-high': '#e4eae5',
        outline: '#707974', 'outline-variant': '#bfc9c3',
        error: '#ba1a1a', 'error-container': '#ffdad6', 'on-error-container': '#93000a',
      },
      chartTones: ['#baffe5', '#96e4c8', '#6dbaa0', '#449179', '#146b55', '#004435'],
    },
    dark: {
      colors: {
        primary: '#88d6bb', 'on-primary': '#00382b', 'primary-container': '#00513f',
        'on-primary-container': '#a3f2d6', secondary: '#b2ccc1', 'secondary-container': '#344c43',
        'on-secondary-container': '#cee9dd',
        surface: '#0f1512', 'on-surface': '#dee4df', 'on-surface-variant': '#bfc9c3',
        'surface-container-lowest': '#090f0d', 'surface-container-low': '#171d1a',
        'surface-container': '#1b211e', 'surface-container-high': '#252b29',
        outline: '#89938e', 'outline-variant': '#3f4945',
        error: '#ffb4ab', 'error-container': '#93000a', 'on-error-container': '#ffdad6',
      },
      chartTones: ['#004c3b', '#04634e', '#388770', '#59a78e', '#7ac8ad', '#a1efd4'],
    },
  },
} satisfies Record<string, Palette>;

export type PaletteId = keyof typeof palettes;
export const paletteIds = Object.keys(palettes) as PaletteId[];
export const paletteStorageKey = 'ridecast-palette';
export const modeStorageKey = 'ridecast-color-mode';

function readPreference(key: string) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function savePreference(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // The theme still works in contexts that restrict browser storage.
  }
}

export function initialPalette(): PaletteId {
  const preference = readPreference(paletteStorageKey);
  return paletteIds.includes(preference as PaletteId) ? preference as PaletteId : 'pixel';
}

export function initialModeOverride(): ColorMode | null {
  const mode = readPreference(modeStorageKey);
  return mode === 'dark' || mode === 'light' ? mode : null;
}

export const differenceTones = {
  light: {
    under: ['#d6e5eb', '#a0c6d8', '#5792b7', '#2c6188'],
    over: ['#f5dfc8', '#e3ad78', '#bc733d', '#804124'],
  },
  dark: {
    under: ['#253b4a', '#3a6076', '#6396b5', '#9bd0eb'],
    over: ['#483427', '#77543a', '#b18155', '#efb98a'],
  },
} as const;
