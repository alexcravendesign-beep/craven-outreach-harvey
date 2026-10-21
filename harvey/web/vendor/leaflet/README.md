Leaflet 1.9.4 (BSD-2-Clause), vendored so the dashboard's Content-Security-Policy
can stay `script-src 'self'`. Files are the unmodified `dist/leaflet.js` and
`dist/leaflet.css` from the 1.9.4 release:

    leaflet.js   sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=
    leaflet.css  sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=

The map draws circle markers only, so Leaflet's marker/layer-control images
are not vendored. Basemap tiles come from OpenStreetMap (see TILE_HOSTS in dashboard_auth.py).
