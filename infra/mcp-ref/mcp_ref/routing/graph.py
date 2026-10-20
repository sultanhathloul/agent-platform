"""The routing fixture as data and two deterministic functions (I10): `geocode` matches a place by
its names in MSA, Gulf and English, folded with app/text/arabic.py (G4); `route` is Dijkstra over
the invented graph, with a fixed speed per profile. Fake data only (G3)."""

import heapq
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.text.arabic import normalize, search_terms

FIXTURE = Path(__file__).resolve().parent / "fixture.yaml"
MAX_MATCHES = 3
MIN_SCORE = 0.5
# The best match is taken as the place meant only when it is strong and clearly ahead.
BEST_MIN_SCORE = 0.85
BEST_MARGIN = 0.15


@dataclass(frozen=True)
class Place:
    id: str
    name_ar: str
    name_en: str
    aliases: tuple[str, ...]
    point: tuple[float, float]
    description: str


class Fixture:
    def __init__(self, path: Path = FIXTURE):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.speeds: dict[str, float] = {k: float(v) for k, v in raw["speeds_kmh"].items()}
        self.places: dict[str, Place] = {}
        for item in raw["places"]:
            self.places[item["id"]] = Place(
                id=item["id"],
                name_ar=item["name"]["ar"],
                name_en=item["name"]["en"],
                aliases=tuple(item["aliases"]),
                point=(float(item["point"][0]), float(item["point"][1])),
                description=item["description"],
            )
        self.edges: dict[str, list[tuple[str, float]]] = {pid: [] for pid in self.places}
        for a, b, km in raw["edges"]:
            if a not in self.places or b not in self.places:
                raise ValueError(f"edge names an unknown place: {a}, {b}")
            self.edges[a].append((b, float(km)))
            self.edges[b].append((a, float(km)))
        self._names = {
            pid: {normalize(alias) for alias in (*p.aliases, p.name_ar, p.name_en)}
            for pid, p in self.places.items()
        }
        self._stems = {
            pid: {" ".join(search_terms(alias)) for alias in (*p.aliases, p.name_ar, p.name_en)}
            for pid, p in self.places.items()
        }
        self._terms = {
            pid: [set(search_terms(alias)) for alias in (*p.aliases, p.name_ar, p.name_en)]
            for pid, p in self.places.items()
        }

    # ------------------------------------------------------------------ geocode

    def _score(self, place_id: str, query: str) -> float:
        folded = normalize(query)
        if not folded:
            return 0.0
        if folded in self._names[place_id]:
            return 1.0
        best = 0.0
        # The query's stems, the article and proclitics stripped («الجامعة» is «جامعه»), inside a
        # name's stems: a partial name that may mean several places.
        stem = " ".join(search_terms(query))
        if len(stem) >= 4:
            for name in self._stems[place_id]:
                if f" {stem} " in f" {name} ":
                    best = max(best, 0.6 + 0.3 * len(stem) / len(name))
        for name in self._names[place_id]:
            # A whole name inside the query ("برج المملكة في العليا"), or the query inside a name.
            if len(name) >= 4 and f" {name} " in f" {folded} ":
                best = max(best, 0.9)
            elif len(folded) >= 4 and folded in name:
                best = max(best, 0.6 + 0.3 * len(folded) / len(name))
        words = set(search_terms(query))
        for terms in self._terms[place_id]:
            if terms and words:
                overlap = len(terms & words) / len(terms | words)
                best = max(best, 0.85 * overlap)
        return round(best, 4)

    def geocode(self, query: str) -> dict:
        scored = sorted(
            ((self._score(pid, query), pid) for pid in self.places), key=lambda item: (-item[0], item[1])
        )
        matches = [self._match(pid, score) for score, pid in scored if score >= MIN_SCORE][:MAX_MATCHES]
        best = None
        if (
            matches
            and matches[0]["score"] >= BEST_MIN_SCORE
            and (len(matches) == 1 or matches[0]["score"] - matches[1]["score"] >= BEST_MARGIN)
        ):
            best = matches[0]
        return {"query": query, "best": best, "matches": matches}

    def _match(self, place_id: str, score: float) -> dict:
        place = self.places[place_id]
        return {
            "id": place.id,
            "name": place.name_ar,
            "name_en": place.name_en,
            "point": list(place.point),
            "score": score,
            "description": place.description,
        }

    # ------------------------------------------------------------------ route

    def resolve(self, ref: str) -> str | None:
        """A place id, or an exact name or alias after folding."""
        if ref in self.places:
            return ref
        folded = normalize(ref)
        return next((pid for pid, names in self._names.items() if folded in names), None)

    def route(self, origin: str, destination: str, profile: str) -> dict | None:
        start, end = self.resolve(origin), self.resolve(destination)
        if start is None or end is None or profile not in self.speeds:
            return None
        distances = {start: 0.0}
        previous: dict[str, str] = {}
        queue = [(0.0, start)]
        while queue:
            distance, node = heapq.heappop(queue)
            if node == end:
                break
            if distance > distances.get(node, float("inf")):
                continue
            for neighbour, km in sorted(self.edges[node]):
                candidate = distance + km
                if candidate < distances.get(neighbour, float("inf")) - 1e-9:
                    distances[neighbour] = candidate
                    previous[neighbour] = node
                    heapq.heappush(queue, (candidate, neighbour))
        if end not in distances:
            return None
        path = [end]
        while path[-1] != start:
            path.append(previous[path[-1]])
        path.reverse()
        steps = []
        for a, b in zip(path, path[1:], strict=False):
            km = min(k for n, k in self.edges[a] if n == b)
            steps.append(
                {"from": self.places[a].name_ar, "to": self.places[b].name_ar, "distance_km": round(km, 1)}
            )
        total = round(distances[end], 1)
        return {
            "from": self.places[start].name_ar,
            "to": self.places[end].name_ar,
            "profile": profile,
            "distance_km": total,
            "duration_min": round(total / self.speeds[profile] * 60),
            "steps": steps,
        }
