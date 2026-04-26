"""
Phase 1 — Data Generator
Generates random facility sales data for Bangladesh (Joypurhat district).
Edit the CSV files later with real data.
"""

import csv
import random
from datetime import datetime, timedelta
import os

random.seed(42)

# ── 1. Facilities ──────────────────────────────────────────────────────────────
# 20 facilities spread across Joypurhat district, Bangladesh
HEALTHCARES = [
    {"facility_id": f"PH{i:03d}",
     "name": name,
     "upazila": upazila,
     "lat": lat + random.uniform(-0.01, 0.01),
     "lon": lon + random.uniform(-0.01, 0.01)}
    for i, (name, upazila, lat, lon) in enumerate([
        ("Asha Facility",         "Joypurhat Sadar", 24.898, 89.017),
        ("Al-Amin Drug House",    "Joypurhat Sadar", 24.902, 89.021),
        ("Noor Medicine Corner",  "Joypurhat Sadar", 24.895, 89.013),
        ("Rahman Facility",       "Joypurhat Sadar", 24.890, 89.025),
        ("City Drug Store",       "Joypurhat Sadar", 24.907, 89.008),
        ("Akbar Medicine",        "Akkelpur",        24.956, 89.045),
        ("Green Cross Facility",  "Akkelpur",        24.961, 89.052),
        ("Sathi Drug House",      "Akkelpur",        24.950, 89.038),
        ("Bismillah Facility",    "Kalai",            25.017, 89.063),
        ("Popular Facility",      "Kalai",            25.022, 89.058),
        ("Hasan Medicine",        "Kalai",            25.011, 89.071),
        ("New Life Facility",     "Khetlal",          24.988, 89.115),
        ("Moon Drug Store",       "Khetlal",          24.981, 89.120),
        ("Rupali Facility",       "Khetlal",          24.994, 89.108),
        ("Al-Shifa Medicine",     "Panchbibi",        25.050, 89.150),
        ("Taher Drug House",      "Panchbibi",        25.055, 89.143),
        ("Health Plus Facility",  "Panchbibi",        25.044, 89.158),
        ("Mamun Medicine Corner", "Panchbibi",        25.060, 89.136),
        ("Riya Facility",         "Joypurhat Sadar",  24.885, 89.030),
        ("Trust Facility",        "Akkelpur",         24.968, 89.041),
    ], start=0)
]

# ── 2. Medicines & their outbreak signals ────────────────────────────────────
MEDICINES = [
    # (name,            base_sales, outbreak_multiplier, signals_disease)
    ("Paracetamol 500mg",  30, 3.5, "Fever/Flu"),
    ("ORS Sachet",         15, 4.0, "Diarrhea"),
    ("Metronidazole 400mg",10, 3.0, "Diarrhea"),
    ("Cetirizine 10mg",    12, 2.5, "Allergy/Fever"),
    ("Amoxicillin 500mg",   8, 2.8, "Respiratory"),
    ("Azithromycin 500mg",  5, 3.2, "Respiratory"),
    ("Zinc 20mg (child)",   8, 3.5, "Diarrhea"),
    ("Ciprofloxacin 500mg", 6, 2.5, "Diarrhea"),
    ("Antihistamine Syrup",10, 2.0, "Allergy/Fever"),
    ("Vitamin C 500mg",    20, 1.8, "Fever/Flu"),
    ("Ranitidine 150mg",   14, 1.5, "Normal"),
    ("Antacid Tablet",     18, 1.3, "Normal"),
    ("Insulin (vial)",      4, 1.1, "Normal"),
    ("Atorvastatin 10mg",   6, 1.0, "Normal"),
    ("Losartan 50mg",       7, 1.0, "Normal"),
]

# ── 3. Simulate a dengue + flu outbreak ──────────────────────────────────────
# Dengue hits Joypurhat Sadar facilities in week 3
# Flu hits Akkelpur in week 4
OUTBREAKS = [
    {"disease": "Dengue",     "start_day": 15, "end_day": 40,
     "upazilas": ["Joypurhat Sadar"],
     "medicines": ["Paracetamol 500mg", "Cetirizine 10mg", "Vitamin C 500mg"]},
    {"disease": "Flu",        "start_day": 22, "end_day": 45,
     "upazilas": ["Akkelpur"],
     "medicines": ["Paracetamol 500mg", "Azithromycin 500mg", "Amoxicillin 500mg"]},
    {"disease": "Diarrhea",   "start_day": 30, "end_day": 55,
     "upazilas": ["Kalai", "Khetlal"],
     "medicines": ["ORS Sachet", "Metronidazole 400mg", "Zinc 20mg (child)", "Ciprofloxacin 500mg"]},
]

def get_multiplier(facility, medicine_name, day):
    """Return sales multiplier if an outbreak affects this facility/medicine."""
    for ob in OUTBREAKS:
        if (facility["upazila"] in ob["upazilas"]
                and medicine_name in ob["medicines"]
                and ob["start_day"] <= day <= ob["end_day"]):
            # ramp up, peak, ramp down
            peak = (ob["start_day"] + ob["end_day"]) // 2
            dist = abs(day - peak)
            span = (ob["end_day"] - ob["start_day"]) / 2
            strength = max(0, 1 - dist / span)
            for m in MEDICINES:
                if m[0] == medicine_name:
                    return 1 + (m[2] - 1) * strength
    return 1.0

# ── 4. Generate sales CSV ─────────────────────────────────────────────────────
START_DATE = datetime(2024, 1, 1)
NUM_DAYS   = 60

sales_rows = []
for day in range(NUM_DAYS):
    date = (START_DATE + timedelta(days=day)).strftime("%Y-%m-%d")
    for ph in HEALTHCARES:
        for (med_name, base, _, _disease) in MEDICINES:
            mult   = get_multiplier(ph, med_name, day)
            noise  = random.uniform(0.7, 1.3)
            qty    = max(0, int(base * mult * noise))
            if qty == 0:
                continue
            sales_rows.append({
                "date":          date,
                "facility_id":   ph["facility_id"],
                "medicine_name": med_name,
                "quantity_sold": qty,
                "upazila":       ph["upazila"],
            })

# ── 5. Write files ────────────────────────────────────────────────────────────
out = os.path.dirname(__file__)

with open(f"{out}/facilities.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["facility_id","name","upazila","lat","lon"])
    w.writeheader(); w.writerows(HEALTHCARES)
print(f"✓ facilities.csv  — {len(HEALTHCARES)} facilities")

with open(f"{out}/sales.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["date","facility_id","medicine_name","quantity_sold","upazila"])
    w.writeheader(); w.writerows(sales_rows)
print(f"✓ sales.csv       — {len(sales_rows):,} records ({NUM_DAYS} days × {len(HEALTHCARES)} facilities)")

with open(f"{out}/medicines.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["medicine_name","base_daily_sales","outbreak_multiplier","signals_disease"])
    w.writeheader()
    for (n, b, m, d) in MEDICINES:
        w.writerow({"medicine_name":n,"base_daily_sales":b,"outbreak_multiplier":m,"signals_disease":d})
print(f"✓ medicines.csv   — {len(MEDICINES)} medicines")

with open(f"{out}/outbreaks_ground_truth.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["disease","start_day","end_day","upazilas","medicines"])
    w.writeheader()
    for ob in OUTBREAKS:
        w.writerow({**ob,
                    "upazilas":  "|".join(ob["upazilas"]),
                    "medicines": "|".join(ob["medicines"])})
print(f"✓ outbreaks_ground_truth.csv  — {len(OUTBREAKS)} simulated outbreaks")


