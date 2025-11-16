from fastapi import FastAPI, HTTPException, Query
import requests
from pyproj import Transformer
from fastapi.middleware.cors import CORSMiddleware
import csv
import os

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

BASE_DIR = os.path.dirname(__file__)
CSV_PATH = os.path.join(BASE_DIR, "grid_scores.csv")

CASES: list[dict] = []

# Commentaires par classe 1–10 (placeholders)
CLASS10_COMMENTS = {
    1: "Accessibilité très faible",
    2: "Accessibilité faible",
    3: "Inférieure à la moyenne",
    4: "Légèrement inférieure à la moyenne",
    5: "Moyenne",
    6: "Légèrement supérieure à la moyenne",
    7: "Bonne accessibilité",
    8: "Très bonne accessibilité",
    9: "Excellente accessibilité",
    10: "Accessibilité exceptionnelle",
}


def load_cases():
    """
    Charge les cases depuis grid_scores.csv (séparateur ;, décimales ,).

    Colonnes obligatoires :
    - id
    - X_LB2008
    - Y_LB2008
    - ms_len

    Colonnes optionnelles utilisées pour l'analyse :
    - Score TC total sans TGV 24h %
    - Score TC train (SNCB) 24h %
    - Score TC MTB 24h %
    - Score TC total sans TGV 24h_Classe_10
    - Score TC train (SNCB) 24h_Classe_10
    - Score TC MTB 24h_Classe_10
    """
    if not os.path.exists(CSV_PATH):
        raise SystemExit(f"Fichier CSV introuvable : {CSV_PATH}")

    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=';')

        if not reader.fieldnames:
            raise SystemExit("Impossible de lire les en-têtes du CSV.")

        raw_headers = reader.fieldnames
        header_map = {h.strip(): h for h in raw_headers}

        required = ["id", "X_LB2008", "Y_LB2008", "ms_len"]
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

        optional_cols = [
            "Score TC total sans TGV 24h %",
            "Score TC train (SNCB) 24h %",
            "Score TC MTB 24h %",
            "Score TC total sans TGV 24h_Classe_10",
            "Score TC train (SNCB) 24h_Classe_10",
            "Score TC MTB 24h_Classe_10",
        ]

        for row in reader:
            try:
                id_val = int(row[id_key])
                x_center = float(row[x_key].replace(",", "."))
                y_center = float(row[y_key].replace(",", "."))
                size = float(row[len_key].replace(",", "."))
            except Exception:
                continue

            extras = {}
            for col in optional_cols:
                raw_col_name = header_map.get(col, col)
                if raw_col_name in row and row[raw_col_name] is not None and row[raw_col_name].strip() != "":
                    val_str = row[raw_col_name].replace(",", ".")
                    try:
                        if col.endswith("_Classe_10"):
                            extras[col] = int(float(val_str))
                        else:
                            extras[col] = round(float(val_str), 2)
                    except Exception:
                        extras[col] = None
                else:
                    extras[col] = None

            half = size / 2.0

            case = {
                "id": id_val,
                # score principal = classe 10 totale (si disponible)
                "score": extras.get("Score TC total sans TGV 24h_Classe_10"),
                "x_min": x_center - half,
                "x_max": x_center + half,
                "y_min": y_center - half,
                "y_max": y_center + half,
                "center_x": x_center,
                "center_y": y_center,
                "size": size,
                "extras": extras,
            }
            CASES.append(case)

    if not CASES:
        raise SystemExit("Aucune case valide trouvée dans grid_scores.csv.")


load_cases()

# ------------------- Utilitaires -------------------


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

    return float(data[0]["lon"]), float(data[0]["lat"])


def find_case_for_point(x, y):
    for c in CASES:
        if (c["x_min"] <= x < c["x_max"]) and (c["y_min"] <= y < c["y_max"]):
            return c
    return None


def classify(score10: int | None):
    """
    Classe lisible basée sur le score sur 10 (total).
    Tu peux ajuster plus tard la logique.
    """
    if score10 is None:
        return None
    if score10 <= 2:
        return "très faible"
    if score10 <= 4:
        return "faible à moyenne"
    if score10 <= 6:
        return "moyenne à correcte"
    if score10 <= 8:
        return "bonne à très bonne"
    return "excellente"


def build_accessibility_analysis(case: dict) -> dict:
    e = case["extras"]

    total_pct = e.get("Score TC total sans TGV 24h %")
    train_pct = e.get("Score TC train (SNCB) 24h %")
    mtb_pct = e.get("Score TC MTB 24h %")

    total10 = e.get("Score TC total sans TGV 24h_Classe_10")
    train10 = e.get("Score TC train (SNCB) 24h_Classe_10")
    mtb10 = e.get("Score TC MTB 24h_Classe_10")

    analysis = {
        "total": {
            "percentile": total_pct,
            "score10": total10,
            "score10_comment": CLASS10_COMMENTS.get(total10, None)
        },
        "train": {
            "percentile": train_pct,
            "score10": train10,
            "score10_comment": CLASS10_COMMENTS.get(train10, None)
        },
        "mtb": {
            "percentile": mtb_pct,
            "score10": mtb10,
            "score10_comment": CLASS10_COMMENTS.get(mtb10, None)
        }
    }

    return analysis


# ------------------- Endpoints -------------------


@app.get("/score_by_address")
def score_by_address(address: str = Query(..., min_length=4)):
    lon, lat = geocode_belgium(address)
    x, y = transformer.transform(lon, lat)

    case = find_case_for_point(x, y)
    if case is None:
        raise HTTPException(404, "Adresse hors de la zone de la grille")

    score10 = case["score"]
    analysis = build_accessibility_analysis(case)

    return {
        "address_input": address,
        "geocoding": {"lon": lon, "lat": lat},
        "lambert2008": {"x": x, "y": y},
        "case": {
            "id": case["id"],
            "score10": score10,
            "classe": classify(score10),
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
        "accessibility_analysis": analysis,
    }


@app.get("/score_structured")
def score_structured(
    street: str = Query(..., min_length=2, description="Rue, avenue, ..."),
    number: str = Query(..., min_length=1, description="Numéro de maison"),
    postal_code: str = Query(..., min_length=4, max_length=4, description="Code postal à 4 chiffres"),
    city: str | None = Query(None, description="Commune (facultatif, mais recommandé)")
):
    if city:
        full_address = f"{street} {number}, {postal_code} {city}, Belgique"
    else:
        full_address = f"{street} {number}, {postal_code}, Belgique"

    lon, lat = geocode_belgium(full_address)
    x, y = transformer.transform(lon, lat)

    case = find_case_for_point(x, y)
    if case is None:
        raise HTTPException(404, "Adresse hors de la zone de la grille")

    score10 = case["score"]
    analysis = build_accessibility_analysis(case)

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
            "score10": score10,
            "classe": classify(score10),
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
        "accessibility_analysis": analysis,
    }
