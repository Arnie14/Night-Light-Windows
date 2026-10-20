# SPDX-License-Identifier: GPL-3.0-only
"""Regenerates nightlight/data/timezones.csv and zone_aliases.csv from the IANA tz database.

    python tools/make_timezones.py /usr/share/zoneinfo

Reads zone.tab (zone, country code, coordinates of a representative city) and
iso3166.tab (country names) and writes decimal degrees, so the app needs
neither tzdata's tab files nor a geocoder to turn a time zone into a location.
tzdata.zi supplies the old names of zones ("Asia/Calcutta" for "Asia/Kolkata"),
which Windows may still report as the system time zone.
"""

import csv
import re
import sys
from pathlib import Path


def decimal(text: str) -> tuple[float, float]:
    """ISO 6709 "+DDMM+DDDMM" or "+DDMMSS+DDDMMSS" to (latitude, longitude)."""
    match = re.fullmatch(r"([+-])(\d{2})(\d{2})(\d{2})?([+-])(\d{3})(\d{2})(\d{2})?", text)
    assert match, text
    ns, lat_d, lat_m, lat_s, ew, lon_d, lon_m, lon_s = match.groups()
    lat = int(lat_d) + int(lat_m) / 60 + int(lat_s or 0) / 3600
    lon = int(lon_d) + int(lon_m) / 60 + int(lon_s or 0) / 3600
    return (-lat if ns == "-" else lat), (-lon if ew == "-" else lon)


def rows(tab: Path):
    for line in tab.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            yield line.split("\t")


def main(zoneinfo: Path) -> None:
    countries = dict(rows(zoneinfo / "iso3166.tab"))
    data = Path(__file__).resolve().parent.parent / "nightlight" / "data"
    zones = set()
    with (data / "timezones.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["zone", "country", "latitude", "longitude"])
        for code, coords, zone, *_ in sorted(rows(zoneinfo / "zone.tab"), key=lambda r: r[2]):
            lat, lon = decimal(coords)
            writer.writerow([zone, countries[code], f"{lat:.4f}", f"{lon:.4f}"])
            zones.add(zone)

    # "L <target> <alias>" lines of tzdata.zi; aliases of aliases are not chained.
    links = [line.split()[1:] for line in (zoneinfo / "tzdata.zi").read_text(encoding="utf-8").splitlines() if line.startswith("L ")]
    with (data / "zone_aliases.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["alias", "zone"])
        for target, alias in sorted(links, key=lambda link: link[1]):
            if target in zones and alias not in zones:
                writer.writerow([alias, target])


if __name__ == "__main__":
    main(Path(sys.argv[1]))
