"""
Khulna District — Shared Dataset Generator
============================================
Generates one dataset used by BOTH:
  1. Your model      → ST-GNN outbreak prediction
  2. Friend's model  → Prophet/Linear forecasting + stock sharing

All CSVs are compatible with both codebases.
"""

import csv
import random
from datetime import datetime, timedelta
import os

random.seed(99)

OUT = os.path.dirname(os.path.abspath(__file__))

# ── 1. Facilities — Khulna District ──────────────────────────────────────────
# 5 upazilas of Khulna with real approximate GPS coordinates
HEALTHCARES = []
raw = [
    # (name,                      upazila,          base_lat,  base_lon)
    ("Al-Amin Facility",          "Khulna Sadar",   22.8456,   89.5403),
    ("Rahman Drug House",         "Khulna Sadar",   22.8501,   89.5441),
    ("Noor Medicine Corner",      "Khulna Sadar",   22.8389,   89.5367),
    ("City Facility",             "Khulna Sadar",   22.8523,   89.5489),
    ("Popular Drug Store",        "Khulna Sadar",   22.8412,   89.5312),
    ("Bismillah Facility",        "Sonadanga",      22.8678,   89.5534),
    ("New Life Medicine",         "Sonadanga",      22.8712,   89.5567),
    ("Sathi Drug House",          "Sonadanga",      22.8634,   89.5501),
    ("Health Plus Facility",      "Sonadanga",      22.8756,   89.5489),
    ("Trust Facility",            "Daulatpur",      22.8923,   89.5234),
    ("Al-Shifa Medicine",         "Daulatpur",      22.8967,   89.5189),
    ("Moon Drug Store",           "Daulatpur",      22.8845,   89.5278),
    ("Green Cross Facility",      "Daulatpur",      22.9012,   89.5145),
    ("Rupali Facility",           "Khalishpur",     22.8234,   89.5089),
    ("Akbar Medicine Corner",     "Khalishpur",     22.8189,   89.5123),
    ("Hasan Drug House",          "Khalishpur",     22.8267,   89.5056),
    ("Mamun Facility",            "Khalishpur",     22.8145,   89.5167),
    ("Riya Medicine Store",       "Batiaghata",     22.7923,   89.4834),
    ("Asha Facility",             "Batiaghata",     22.7867,   89.4789),
    ("Life Care Drug House",      "Batiaghata",     22.7978,   89.4878),
]

for i, (name, upazila, lat, lon) in enumerate(raw):
    HEALTHCARES.append({
        "facility_id": f"PH{i:03d}",
        "name":        name,
        "upazila":     upazila,
        "lat":         round(lat + random.uniform(-0.003, 0.003), 6),
        "lon":         round(lon + random.uniform(-0.003, 0.003), 6),
    })

# ── 2. Medicines ──────────────────────────────────────────────────────────────
# (name, base_daily_sales, outbreak_multiplier, signals_disease)
MEDICINES = [
    ("Paracetamol 500mg",     35,  3.5, "Fever/Flu"),
    ("ORS Sachet",            18,  4.2, "Diarrhea"),
    ("Metronidazole 400mg",   12,  3.1, "Diarrhea"),
    ("Cetirizine 10mg",       14,  2.6, "Allergy/Fever"),
    ("Amoxicillin 500mg",     10,  2.9, "Respiratory"),
    ("Azithromycin 500mg",     6,  3.3, "Respiratory"),
    ("Zinc 20mg (child)",     10,  3.6, "Diarrhea"),
    ("Ciprofloxacin 500mg",    7,  2.6, "Diarrhea"),
    ("Antihistamine Syrup",   11,  2.1, "Allergy/Fever"),
    ("Vitamin C 500mg",       22,  1.9, "Fever/Flu"),
    ("Ranitidine 150mg",      16,  1.4, "Normal"),
    ("Antacid Tablet",        20,  1.2, "Normal"),
    ("Insulin (vial)",         5,  1.1, "Normal"),
    ("Atorvastatin 10mg",      7,  1.0, "Normal"),
    ("Losartan 50mg",          8,  1.0, "Normal"),
]

# ── 3. Outbreaks — Khulna realistic diseases ──────────────────────────────────
# Khulna is coastal → dengue, cholera, diarrhea very common
OUTBREAKS = [
    {
        "disease":   "Dengue",
        "start_day": 10,
        "end_day":   38,
        "upazilas":  ["Khulna Sadar", "Sonadanga"],
        "medicines": ["Paracetamol 500mg", "Cetirizine 10mg", "Vitamin C 500mg"],
    },
    {
        "disease":   "Cholera/Diarrhea",
        "start_day": 25,
        "end_day":   52,
        "upazilas":  ["Batiaghata", "Khalishpur"],
        "medicines": ["ORS Sachet", "Metronidazole 400mg",
                      "Zinc 20mg (child)", "Ciprofloxacin 500mg"],
    },
    {
        "disease":   "Respiratory Infection",
        "start_day": 40,
        "end_day":   65,
        "upazilas":  ["Daulatpur"],
        "medicines": ["Amoxicillin 500mg", "Azithromycin 500mg",
                      "Paracetamol 500mg"],
    },
]

# ── 4. Helper — outbreak sales multiplier ─────────────────────────────────────
def get_multiplier(facility, medicine_name, day):
    for ob in OUTBREAKS:
        if (facility["upazila"] in ob["upazilas"]
                and medicine_name in ob["medicines"]
                and ob["start_day"] <= day <= ob["end_day"]):
            peak   = (ob["start_day"] + ob["end_day"]) // 2
            span   = (ob["end_day"] - ob["start_day"]) / 2
            dist   = abs(day - peak)
            strength = max(0, 1 - dist / span)
            for (mn, _, mult, _) in MEDICINES:
                if mn == medicine_name:
                    return 1 + (mult - 1) * strength
    return 1.0

# ── 5. Generate sales ─────────────────────────────────────────────────────────
START_DATE = datetime(2024, 1, 1)
NUM_DAYS   = 90          # 3 months — good for both forecasting + GNN training

sales_rows = []
for day in range(NUM_DAYS):
    date = (START_DATE + timedelta(days=day)).strftime("%Y-%m-%d")
    for ph in HEALTHCARES:
        for (med_name, base, _, _disease) in MEDICINES:
            mult  = get_multiplier(ph, med_name, day)
            noise = random.uniform(0.75, 1.25)
            qty   = max(0, int(base * mult * noise))
            if qty == 0:
                continue
            sales_rows.append({
                "date":          date,
                "facility_id":   ph["facility_id"],
                "medicine_name": med_name,
                "quantity_sold": qty,
                "upazila":       ph["upazila"],
            })

# ── 6. Write all CSVs ─────────────────────────────────────────────────────────

# facilities.csv
with open(f"{OUT}/facilities.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["facility_id","name","upazila","lat","lon"])
    w.writeheader()
    w.writerows(HEALTHCARES)
print(f"✓ facilities.csv        — {len(HEALTHCARES)} facilities across 5 Khulna upazilas")

# sales.csv
with open(f"{OUT}/sales.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["date","facility_id","medicine_name",
                                       "quantity_sold","upazila"])
    w.writeheader()
    w.writerows(sales_rows)
print(f"✓ sales.csv             — {len(sales_rows):,} records "
      f"({NUM_DAYS} days × {len(HEALTHCARES)} facilities × {len(MEDICINES)} medicines)")

# medicines.csv
with open(f"{OUT}/medicines.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["medicine_name","base_daily_sales",
                                       "outbreak_multiplier","signals_disease"])
    w.writeheader()
    for (n, b, m, d) in MEDICINES:
        w.writerow({"medicine_name": n, "base_daily_sales": b,
                    "outbreak_multiplier": m, "signals_disease": d})
print(f"✓ medicines.csv         — {len(MEDICINES)} medicines")

# outbreaks_ground_truth.csv
with open(f"{OUT}/outbreaks_ground_truth.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["disease","start_day","end_day",
                                       "upazilas","medicines"])
    w.writeheader()
    for ob in OUTBREAKS:
        w.writerow({
            "disease":   ob["disease"],
            "start_day": ob["start_day"],
            "end_day":   ob["end_day"],
            "upazilas":  "|".join(ob["upazilas"]),
            "medicines": "|".join(ob["medicines"]),
        })
print(f"✓ outbreaks_ground_truth.csv — {len(OUTBREAKS)} outbreaks "
      f"(Dengue, Cholera/Diarrhea, Respiratory)")

print()
print("Dataset summary:")
print(f"  District  : Khulna")
print(f"  Upazilas  : Khulna Sadar, Sonadanga, Daulatpur, Khalishpur, Batiaghata")
print(f"  Facilities: {len(HEALTHCARES)}")
print(f"  Days      : {NUM_DAYS} (Jan 1 – Mar 31, 2024)")
print(f"  Records   : {len(sales_rows):,}")
print()
print("Compatible with:")
print("  ✓ Your model      → ST-GNN outbreak prediction (run_all.py)")
print("  ✓ Friend's model  → Prophet forecasting (forecast_hrsp.py)")


