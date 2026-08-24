"""Generate the browser moon catalogue from public orbital catalogs."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import ssl
import re
import urllib.request
from io import StringIO
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse


DISCOVERY_URL = "https://ssd.jpl.nasa.gov/sats/discovery.html"
ELEMENTS_URL = "https://ssd.jpl.nasa.gov/sats/elem/"
CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle"
CELESTRAK_INDEX_URL = "https://celestrak.org/NORAD/elements/"
CELESTRAK_FALLBACK_URL = "https://www.celestrak.com/NORAD/elements/gp.php?GROUP=active&FORMAT=tle"
OUTPUT = Path(__file__).parents[1] / "web" / "public" / "moons.json"
PLANETS = {"Earth", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune"}
EARTH_MU_KM3_S2 = 398_600.441_8


def fetch(url: str) -> str:
    ssl_context = None
    if "celestrak" in url.lower():
        try:
            import certifi  # type: ignore
            ssl_context = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ssl_context = ssl.create_default_context()
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/plain,text/html,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=30, context=ssl_context) as response:
        return response.read().decode("utf-8")


def normalize_celestrak_endpoint(document: str, source_name: str) -> list[tuple[str, str]]:
    query_urls: list[tuple[str, str]] = []
    for href in re.findall(r"href\s*=\s*[\"']([^\"']+)[\"']", document, flags=re.I):
        href_upper = href.upper()
        if "FORMAT=CSV" not in href_upper:
            continue
        if not href.startswith("gp.php?"):
            continue
        if "GROUP=ACTIVE" in href_upper or "SPECIAL=ACTIVE" in href_upper:
            continue
        if "LAST-30-DAYS" in href_upper or "OLD" in href_upper:
            continue
        query = parse_qs(urlparse(href).query)
        if "GROUP" in query:
            group = query["GROUP"][0].lower()
            endpoint = f"{source_name}gp.php?GROUP={group}&FORMAT=csv"
            query_urls.append((f"group={group}", endpoint))
        elif "SPECIAL" in query:
            special = query["SPECIAL"][0].lower()
            endpoint = f"{source_name}gp.php?SPECIAL={special}&FORMAT=csv"
            query_urls.append((f"special={special}", endpoint))
    seen = set()
    deduped: list[tuple[str, str]] = []
    for marker, endpoint in query_urls:
        if endpoint in seen:
            continue
        seen.add(endpoint)
        deduped.append((marker, endpoint))
    return deduped


def parse_csv_elements(document: str, source_name: str) -> list[dict]:
    values: list[dict] = []
    reader = csv.DictReader(StringIO(document))
    for row in reader:
        mean_motion = number(row.get("MEAN_MOTION", "") or "")
        if mean_motion is None:
            continue
        eccentricity = number(row.get("ECCENTRICITY", "") or "")
        inclination = number(row.get("INCLINATION", "") or "")
        ascending_node = number(row.get("RA_OF_ASC_NODE", "") or "")
        argument = number(row.get("ARG_OF_PERICENTER", "") or "")
        mean_anomaly = number(row.get("MEAN_ANOMALY", "") or "")
        if None in (inclination, ascending_node, argument, mean_anomaly, eccentricity):
            continue
        try:
            semi_major_axis = _mean_motion_to_semimajor_axis_km(mean_motion)
        except ValueError:
            continue

        sat_num = row.get("NORAD_CAT_ID", "") or row.get("OBJECT_ID", "")
        name = row.get("OBJECT_NAME", "").strip()
        if not sat_num or not name:
            continue

        base_id = _slug(name) or f"satellite-{sat_num}"
        satellite_id = f"earth-{base_id}-{sat_num}"
        epoch_value = row.get("EPOCH", "").strip() or None

        values.append(
            {
                "id": satellite_id,
                "name": name,
                "parentId": "earth",
                "provisionalDesignation": sat_num,
                "semiMajorAxisKm": float(semi_major_axis),
                "eccentricity": float(eccentricity),
                "argumentPeriapsisDeg": float(argument),
                "meanAnomalyEpochDeg": float(mean_anomaly),
                "inclinationDeg": float(inclination),
                "ascendingNodeDeg": float(ascending_node),
                "orbitalPeriodDays": abs(1.0 / mean_motion),
                "epoch": epoch_value,
                "orbitSource": f"celestrak-{source_name}",
            },
        )
    return values


def fetch_celestrak() -> str:
    last_error = None
    for url in (CELESTRAK_URL, CELESTRAK_FALLBACK_URL):
        try:
            return fetch(url)
        except Exception as error:  # pragma: no cover - fallback behaviour
            last_error = error
            continue
    if last_error is not None:
        raise last_error
    raise RuntimeError("Keine CelesTrak-Quelle war verfügbar.")


def fetch_celestrak_fallback():
    entries: list[dict] = []
    index_body = fetch(CELESTRAK_INDEX_URL)
    for source_name, endpoint in normalize_celestrak_endpoint(index_body, CELESTRAK_INDEX_URL):
        try:
            payload = parse_csv_elements(fetch(endpoint), source_name)
        except Exception:
            continue
        entries.extend(payload)
    deduped: dict[str, dict] = {}
    for entry in entries:
        deduped[entry["id"]] = entry
    return list(deduped.values())


def text_content(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return " ".join(html.unescape(value).replace("\xa0", " ").split())


def rows(document: str):
    for row_match in re.finditer(r"<tr(?P<attrs>[^>]*)>(?P<body>.*?)</tr>", document, re.I | re.S):
        cells = [
            text_content(cell)
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_match.group("body"), re.I | re.S)
        ]
        if cells:
            yield row_match.group("attrs"), cells


def parse_discoveries(document: str) -> list[dict]:
    moons: list[dict] = [{
        "id": "earth-moon",
        "name": "Moon",
        "parentId": "earth",
        "provisionalDesignation": None,
    }]
    parent = None
    for attrs, cells in rows(document):
        planet_match = re.search(
            r"Satellites of (?:Dwarf Planet )?([A-Za-z]+):\s*\d+",
            " ".join(cells),
        )
        if planet_match:
            candidate = planet_match.group(1)
            parent = candidate if candidate in PLANETS - {"Earth"} else None
            continue
        if not parent or len(cells) < 3 or cells[0] in {"Number", "No."}:
            continue
        name = cells[1] or cells[2]
        if not name:
            continue
        provisional = cells[2] or None
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        moons.append({
            "id": f"{parent.lower()}-{slug}",
            "name": name,
            "parentId": parent.lower(),
            "provisionalDesignation": provisional,
        })
    return moons


def number(value: str) -> float | None:
    cleaned = value.replace(",", "").replace("−", "-").strip()
    cleaned = re.sub(r"[^0-9.eE+\-]", "", cleaned)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _mean_motion_to_semimajor_axis_km(mean_motion_rev_per_day: float) -> float:
    angular_rate = 2 * math.pi * mean_motion_rev_per_day / 86_400
    if angular_rate <= 0:
        raise ValueError("Ungültige Bahngeschwindigkeit.")
    return (EARTH_MU_KM3_S2 / (angular_rate**2)) ** (1.0 / 3.0)


def _tle_epoch_to_iso(value: str) -> str | None:
    raw = value.strip()
    if len(raw) < 5 or "." not in raw:
        return None
    try:
        year = int(raw[:2])
        day_of_year = float(raw[2:])
    except ValueError:
        return None
    full_year = 2000 + year if year < 57 else 1900 + year
    try:
        base = datetime(full_year, 1, 1, tzinfo=timezone.utc) + timedelta(days=day_of_year - 1)
    except (OverflowError, ValueError):
        return None
    return base.date().isoformat()


def parse_elements(document: str) -> dict[tuple[str, str], dict]:
    result: dict[tuple[str, str], dict] = {}
    for _, cells in rows(document):
        # ID, planet, satellite, code, ephemeris, frame, epoch,
        # a, e, argument of periapsis, mean anomaly, inclination, node, period, ...
        if len(cells) < 14 or cells[1] not in PLANETS:
            continue
        semi_major_axis = number(cells[7])
        period = number(cells[13])
        if semi_major_axis is None or period is None:
            continue
        values = {
            "semiMajorAxisKm": semi_major_axis,
            "eccentricity": number(cells[8]) or 0.0,
            "argumentPeriapsisDeg": number(cells[9]) or 0.0,
            "meanAnomalyEpochDeg": number(cells[10]) or 0.0,
            "inclinationDeg": number(cells[11]) or 0.0,
            "ascendingNodeDeg": number(cells[12]) or 0.0,
            "orbitalPeriodDays": abs(period),
            "epoch": cells[6],
            "orbitSource": "jpl-mean-elements",
        }
        for key in {cells[2], cells[3]}:
            if key:
                result[(cells[1].lower(), key.casefold())] = values
    return result


def parse_tle_elements(document: str) -> list[dict]:
    lines = [line.strip() for line in document.splitlines() if line.strip()]
    satellites: list[dict] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("0 ") and index + 2 < len(lines) and lines[index + 1].startswith("1 ") and lines[index + 2].startswith("2 "):
            name = line[2:].strip()
            records = (lines[index + 1], lines[index + 2], name)
            index += 3
        elif line.startswith("1 ") and index + 1 < len(lines) and not lines[index + 1].startswith(("1 ", "2 ")) and index > 0 and not lines[index - 1].startswith("1 ") and not lines[index - 1].startswith("2 "):
            name = lines[index - 1]
            records = (line, lines[index + 1], name)
            index += 2
        else:
            index += 1
            continue

        line1, line2, name = records
        tokens2 = line2.split()
        if not tokens2 or len(tokens2) < 8:
            continue

        sat_num = tokens2[0] if tokens2 else None
        if not sat_num or not sat_num.isdigit():
            sat_num = re.sub(r"\D", "", line1.split()[1]) if len(line1.split()) > 1 else None
        intl = line1.split()[2] if len(line1.split()) > 2 else None
        name_part = name or f"satellite-{sat_num or 'unknown'}"

        inclination = number(tokens2[2])
        ascending_node = number(tokens2[3])
        eccentricity = number("0." + tokens2[4]) if tokens2[4] else None
        argument = number(tokens2[5])
        mean_anomaly = number(tokens2[6])
        mean_motion = number(tokens2[7])
        if None in (inclination, ascending_node, argument, mean_anomaly, eccentricity, mean_motion):
            continue

        try:
            semi_major_axis = _mean_motion_to_semimajor_axis_km(mean_motion)
        except ValueError:
            continue

        epoch_raw = line1.split()
        epoch = _tle_epoch_to_iso(epoch_raw[3]) if len(epoch_raw) > 3 else None
        base_id = _slug(name_part) or f"satellite-{sat_num or 'unknown'}"
        if sat_num:
            satellite_id = f"earth-{base_id}-{sat_num}"
        else:
            satellite_id = f"earth-{base_id}"

        satellites.append({
            "id": satellite_id,
            "name": name_part,
            "parentId": "earth",
            "provisionalDesignation": intl,
            "semiMajorAxisKm": float(semi_major_axis),
            "eccentricity": float(eccentricity),
            "argumentPeriapsisDeg": float(argument),
            "meanAnomalyEpochDeg": float(mean_anomaly),
            "inclinationDeg": float(inclination),
            "ascendingNodeDeg": float(ascending_node),
            "orbitalPeriodDays": abs(1.0 / mean_motion),
            "epoch": epoch,
            "orbitSource": "celestrak-active",
        })
    return satellites


def _section_name(source: str) -> str:
    return {
        "jpl": "JPL-Mean-Elemente",
        "celestrak": "CelesTrak-Aktive Satelliten",
        "both": "Kombiniert (JPL + CelesTrak)",
    }.get(source, "unbekannt")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        choices=["both", "jpl", "celestrak"],
        default="both",
        help="Wähle die Quelle für Satellitendaten.",
    )
    args = parser.parse_args()

    moons = parse_discoveries(fetch(DISCOVERY_URL))
    matched = 0
    matched_celestrak = 0
    source = args.source

    elements = parse_elements(fetch(ELEMENTS_URL)) if source in {"both", "jpl"} else {}
    if source in {"both", "jpl"}:
        for moon in moons:
            candidates = [moon["name"], moon.get("provisionalDesignation")]
            orbit = next(
                (elements[(moon["parentId"], candidate.casefold())]
                 for candidate in candidates
                 if candidate and (moon["parentId"], candidate.casefold()) in elements),
                None,
            )
            if orbit:
                moon.update(orbit)
                matched += 1

    if source in {"both", "celestrak"}:
        existing_ids = {moon["id"] for moon in moons}
        try:
            for satellite in parse_tle_elements(fetch_celestrak()):
                if satellite["id"] not in existing_ids:
                    moons.append(satellite)
                    existing_ids.add(satellite["id"])
                    matched_celestrak += 1
        except Exception as error:
            fallback_satellites = []
            try:
                fallback_satellites = fetch_celestrak_fallback()
            except Exception:
                pass
            if fallback_satellites:
                for satellite in fallback_satellites:
                    if satellite["id"] not in existing_ids:
                        moons.append(satellite)
                        existing_ids.add(satellite["id"])
                        matched_celestrak += 1
                if source != "celestrak":
                    print(f"[WARN] CelesTrak-Fallback aktiv: {error}")
                print(f"[INFO] CelesTrak via Gruppen-CSV geladen: {matched_celestrak} Einträge.")
            else:
                if source == "celestrak":
                    raise error
                print(f"[WARN] CelesTrak konnte nicht geladen werden: {error}")

    for moon in moons:
        if source in {"jpl", "both"} and "semiMajorAxisKm" not in moon and moon.get("orbitSource") == "jpl-mean-elements":
            moon["orbitSource"] = "jpl-mean-elements (unbestätigt)"

    counts = {
        planet: sum(moon["parentId"] == planet for moon in moons)
        for planet in ("mercury", "venus", "earth", "mars", "jupiter", "saturn", "uranus", "neptune")
    }
    counts["earthArtificial"] = sum(
        moon.get("parentId") == "earth" and (moon.get("orbitSource") or "").startswith("celestrak")
        for moon in moons
    )
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": {
            "catalogue": DISCOVERY_URL,
            "meanElements": ELEMENTS_URL if source in {"both", "jpl"} else None,
            "celestrak": CELESTRAK_URL if source in {"both", "celestrak"} else None,
            "selection": source,
        },
        "sourceSummary": _section_name(source),
        "total": len(moons),
        "withJplElements": matched,
        "withCelestrakElements": matched_celestrak,
        "counts": counts,
        "moons": moons,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(moons)} moons ({matched} with orbital elements) to {OUTPUT}")
    print(counts)


if __name__ == "__main__":
    main()
