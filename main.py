from fastapi import FastAPI, HTTPException, Query
import requests
from pyproj import Transformer
import os
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------- Géocodage via Nominatim -------------------

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

transformer = Transformer.from_crs("EPSG:4326", "EPSG:3812", always_xy=True)

# ------------------- Chargement de la grille -------------------

import csv
import os

BASE_DIR = os.path.dirname(__file__)
CSV_PATH = os.path.join(BASE_DIR, "grid_scores.csv")

CASES: list[dict] = []


def load_cases():
    """
    Charge les cases depuis grid_scores.csv (séparateur ;, décimales ,).
    Colonnes obligatoires : id, X_LB2008, Y_LB2008, ms_len, score
    """
    if not os.path.exists(CSV_PATH):
        raise SystemExit(f"Fichier CSV introuvable : {CSV_PATH}")

    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=';')

        if not reader.fieldnames:
            raise SystemExit("Impossible de lire les en-têtes du CSV.")

        # Normaliser les noms d'en-tête (trim) et construire une map
        raw_headers = reader.fieldnames
        header_map = {h.strip(): h for h in raw_headers}

        required = ["id", "X_LB2008", "Y_LB2008", "ms_len", "score"]
        missing = [r for r in required if r not in header_map]
        if missing:
            raise SystemExit(
                f"Colonnes manquantes dans grid_scores.csv : {missing}. "
                f"En-têtes trouvées : {raw_headers}"
            )

        id_key = header_map["id"]
        x_key = header_map["X_LB2008"]
        y_key = header_map["Y_LB2008"]
        len_key = header_map["ms_len"]
        score_key = header_map["score"]

        for row in reader:
            try:
                id_val = int(row[id_key])

                # ex: "525484,4046" -> 525484.4046
                x_center = float(row[x_key].replace(",", "."))
                y_center = float(row[y_key].replace(",", "."))
                size = float(row[len_key].replace(",", "."))
                score = float(row[score_key].replace(",", "."))
            except (TypeError, ValueError, KeyError, AttributeError):
                # Ligne invalide → on skip
                continue

            half = size / 2.0

            case = {
                "id": id_val,
                "score": score,
                "x_min": x_center - half,
                "x_max": x_center + half,
                "y_min": y_center - half,
                "y_max": y_center + half,
                "center_x": x_center,
                "center_y": y_center,
                "size": size,
            }
            CASES.append(case)

    if not CASES:
        raise SystemExit(
            f"Aucune case valide trouvée dans grid_scores.csv. "
            f"Vérifie le contenu (id;ms_len;...;X_LB2008;Y_LB2008;score)."
        )

# Charger la grille au démarrage
load_cases()

# ------------------- Fonctions utilitaires -------------------

@app.get("/ping")
def ping():
    return {"ok": True, "nb_cases": len(CASES)}


def geocode_belgium(address: str):
    r = requests.get(
        NOMINATIM_URL,
        params={
            "q": address,
            "format": "json",
            "addressdetails": 1,
            "countrycodes": "be",
            "limit": 1,
        },
        headers={"User-Agent": "AccessTC-app/1.0"},
        timeout=10
    )
    data = r.json()

    if not data:
        raise HTTPException(404, "Adresse introuvable")

    lon = float(data[0]["lon"])
    lat = float(data[0]["lat"])
    return lon, lat


def find_case_for_point(x, y):
    for c in CASES:
        if (c["x_min"] <= x < c["x_max"]) and (c["y_min"] <= y < c["y_max"]):
            return c
    return None


def classify(score):
    if score < 1000:
        return "Perdu dans la pampa"
    if score < 3:
        return "moyen"
    if score < 6:
        return "bon"
    return "mystère"


# ------------------- Endpoint principal -------------------

@app.get("/score_by_address")
def score_by_address(address: str = Query(..., min_length=4)):
    lon, lat = geocode_belgium(address)
    x, y = transformer.transform(lon, lat)

    case = find_case_for_point(x, y)
    if case is None:
        raise HTTPException(404, "Adresse hors de la zone de la grille")

    score = case["score"]

    return {
        "address_input": address,
        "geocoding": {"lon": lon, "lat": lat},
        "lambert2008": {"x": x, "y": y},
        "case": {
            "id": case["id"],
            "score": score,
            "classe": classify(score),
            "center_lambert2008": {
                "x_center": case["center_x"],
                "y_center": case["center_y"],
            },
            "bounds_lambert2008": {
                "x_min": case["x_min"],
                "x_max": case["x_max"],
                "y_min": case["y_min"],
                "y_max": case["y_max"],
            },
            "size_meters": case["size"],
        },
    }
@app.get("/score_structured")
def score_structured(
    street: str = Query(..., min_length=2, description="Rue, avenue, ..."),
    number: str = Query(..., min_length=1, description="Numéro de maison"),
    postal_code: str = Query(..., min_length=4, max_length=4, description="Code postal à 4 chiffres"),
    city: str | None = Query(None, description="Commune (facultatif, mais recommandé)")
):
    """
    Variante de /score_by_address avec adresse structurée.
    Tu fournis street, number, postal_code (+ éventuellement city),
    et on construit une adresse complète pour le géocodage.
    """

    if city:
        full_address = f"{street} {number}, {postal_code} {city}, Belgique"
    else:
        full_address = f"{street} {number}, {postal_code}, Belgique"

    lon, lat = geocode_belgium(full_address)
    x, y = transformer.transform(lon, lat)

    case = find_case_for_point(x, y)
    if case is None:
        raise HTTPException(404, "Adresse hors de la zone de la grille")

    score = case["score"]

    return {
        "address_input_structured": {
            "street": street,
            "number": number,
            "postal_code": postal_code,
            "city": city,
        },
        "address_built_for_geocoding": full_address,
        "geocoding": {"lon": lon, "lat": lat},
        "lambert2008": {"x": x, "y": y},
        "case": {
            "id": case["id"],
            "score": score,
            "classe": classify(score),
            "center_lambert2008": {
                "x_center": case["center_x"],
                "y_center": case["center_y"],
            },
            "bounds_lambert2008": {
                "x_min": case["x_min"],
                "x_max": case["x_max"],
                "y_min": case["y_min"],
                "y_max": case["y_max"],
            },
            "size_meters": case["size"],
        },
    }
