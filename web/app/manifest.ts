import type { MetadataRoute } from 'next';

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'Zenbree',
    short_name: 'Zenbree',
    start_url: '/',
    display: 'standalone',
    background_color: '#f8fcfb',
    theme_color: '#006679',
    icons: [
      { src: '/icon.svg', sizes: 'any', type: 'image/svg+xml' },
      { src: '/apple-icon.png', sizes: '180x180', type: 'image/png' },
    ],
  };
}
