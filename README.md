# circeo-meteo

Extraction of multi-year North-Atlantic 10 m wind data (reanalysis and forecasts) into compact
NetCDF files, run on GitHub Actions and published as GitHub Release assets of this public repository
(redistribution with attribution, see `docs/release_notes.md`). No client information.

| Dataset (`config.yaml`) | Source | Licence | Period | Storage grid |
|---|---|---|---|---|
| `era5` | ARCO-ERA5 (Google public Zarr) | Copernicus (commercial use OK, attribution) | 1995-2025 | 0.5°, hourly |
| `gefs_reforecast` | NOAA GEFSv12 reforecast (AWS) | NOAA open data | 2000-2019 | 1.0°, 0-240 h / 6 h, 5 members |
| `gefs_oper` | NOAA GEFS operational (AWS) | NOAA open data | 2021-2025 | 1.0°, 00 UTC, control + 10 |
| `ecmwf_hres_wb2` | ECMWF IFS HRES via WeatherBench 2 | **check bucket LICENSE** | 2016-2022 | 0.5°, 00/12 UTC |
| `ecmwf_open` | ECMWF open data (AWS mirror) | CC-BY-4.0 | 2024 → | HRES/AIFS 0.5°, ENS 1.0° |

Box: 30-70°N, 65°W-10°E. Variables `u10`, `v10` (m/s, wind towards), int16 × 0.01, zlib.

* `meteo/` — builders (`python -m meteo.build <dataset> --year Y [--month M]`).
* `.github/workflows/build.yml` — manual historical extraction, matrix over years/months,
  uploads to this repository's Releases with `GITHUB_TOKEN`.
* `.github/workflows/daily.yml` + `meteo/aggregate.py` — every day, fetch the missing runs of the
  last 3 days (ECMWF IFS HRES, IFS ENS, AIFS; GEFS) and merge them into monthly files.
* `client/fetch_meteo.py` — download with cache + sha256 check (`gh` CLI).
* `docs/MODUS_OPERANDI_fr.md` — sources, durations, volumes and step-by-step procedure.

Attribution: contains modified Copernicus Climate Change Service information; ECMWF data adapted
under CC BY 4.0; NOAA/NCEP GEFS data (values regridded, not endorsed by NOAA).
