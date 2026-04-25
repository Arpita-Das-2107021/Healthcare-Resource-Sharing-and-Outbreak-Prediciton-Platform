"""
Bangladesh National Dataset Generator
Generates simulated facility sales, outbreaks, and social media data for the whole country.
"""

import csv
import random
from datetime import datetime, timedelta
import os

random.seed(42)
OUT = os.path.dirname(os.path.abspath(__file__))

# ── 1. Facilities — Nationwide (8 Divisions) ──────────────────────────────────
REGIONS = [
    ("Dhaka", 23.8103, 90.4125), ("Chittagong", 22.3569, 91.7832),
    ("Rajshahi", 24.3745, 88.6042), ("Khulna", 22.8456, 89.5403),
    ("Barisal", 22.7010, 90.3535), ("Sylhet", 24.8949, 91.8687),
    ("Rangpur", 25.7439, 89.2752), ("Mymensingh", 24.7471, 90.4203)
]

FACILITIES = []
facility_id = 1
for region, base_lat, base_lon in REGIONS:
    for i in range(30):
        FACILITIES.append({
            "facility_id": f"PH{facility_id:04d}", # Upgraded to 4 digits (e.g. PH0001)
            "name": f"{region} Care Facility {i+1}",
            "upazila": region,
            # Increased the GPS spread so they span across the whole city/region
            "lat": round(base_lat + random.uniform(-0.15, 0.15), 6),
            "lon": round(base_lon + random.uniform(-0.15, 0.15), 6)
        })
        facility_id += 1
# ── 2. Medicines (Using your exact provided list) ─────────────────────────────
MEDICINES = [
    ("Paracetamol 500mg",     30, 3.5, "Fever/Flu"),
    ("ORS Sachet",            15, 4.0, "Diarrhea"),
    ("Metronidazole 400mg",   10, 3.0, "Diarrhea"),
    ("Cetirizine 10mg",       12, 2.5, "Allergy/Fever"),
    ("Amoxicillin 500mg",      8, 2.8, "Respiratory"),
    ("Azithromycin 500mg",     5, 3.2, "Respiratory"),
    ("Zinc 20mg (child)",      8, 3.5, "Diarrhea"),
    ("Ciprofloxacin 500mg",    6, 2.5, "Diarrhea"),
    ("Antihistamine Syrup",   10, 2.0, "Allergy/Fever"),
    ("Vitamin C 500mg",       20, 1.8, "Fever/Flu"),
    ("Ranitidine 150mg",      14, 1.5, "Normal"),
    ("Antacid Tablet",        18, 1.3, "Normal"),
    ("Insulin (vial)",         4, 1.1, "Normal"),
    ("Atorvastatin 10mg",      6, 1.0, "Normal"),
    ("Losartan 50mg",          7, 1.0, "Normal"),
]

# ── 3. National Outbreaks ─────────────────────────────────────────────────────
OUTBREAKS = [
    {"disease": "Dengue", "start_day": 15, "end_day": 45, 
     "upazilas": ["Dhaka", "Chittagong"], 
     "medicines": ["Paracetamol 500mg", "Cetirizine 10mg", "Vitamin C 500mg"]},
    {"disease": "Cholera", "start_day": 30, "end_day": 60, 
     "upazilas": ["Barisal", "Khulna"], 
     "medicines": ["ORS Sachet", "Metronidazole 400mg", "Zinc 20mg (child)", "Ciprofloxacin 500mg"]},
    {"disease": "Flu/Respiratory", "start_day": 10, "end_day": 35, 
     "upazilas": ["Sylhet", "Rangpur"], 
     "medicines": ["Amoxicillin 500mg", "Azithromycin 500mg", "Paracetamol 500mg"]},
]

def get_outbreak_strength(region, med_name, day):
    for ob in OUTBREAKS:
        if region in ob["upazilas"] and med_name in ob["medicines"] and ob["start_day"] <= day <= ob["end_day"]:
            peak = (ob["start_day"] + ob["end_day"]) / 2
            span = (ob["end_day"] - ob["start_day"]) / 2
            strength = max(0, 1 - abs(day - peak) / span)
            for (mn, _, mult, _) in MEDICINES:
                if mn == med_name: return 1 + (mult - 1) * strength
    return 1.0

def get_social_media_spike(region, disease, day):
    for ob in OUTBREAKS:
        if region in ob["upazilas"] and ob["disease"] == disease and ob["start_day"] <= day <= ob["end_day"]:
            peak = (ob["start_day"] + ob["end_day"]) / 2
            span = (ob["end_day"] - ob["start_day"]) / 2
            return int(50 * max(0, 1 - abs(day - peak) / span))
    return 0

# ── 4. Generate Data ──────────────────────────────────────────────────────────
START_DATE = datetime(2024, 1, 1)
NUM_DAYS = 90

sales_rows = []
social_media_rows = []

for day in range(NUM_DAYS):
    date = (START_DATE + timedelta(days=day)).strftime("%Y-%m-%d")
    
    # Generate Sales Data
    for facility in FACILITIES:
        for (med_name, base, _, _) in MEDICINES:
            mult = get_outbreak_strength(facility["upazila"], med_name, day)
            qty = max(0, int(base * mult * random.uniform(0.8, 1.2)))
            if qty > 0:
                sales_rows.append(
                    {
                        "date": date,
                        "facility_id": facility["facility_id"],
                        "medicine_name": med_name,
                        "quantity_sold": qty,
                        "upazila": facility["upazila"],
                    }
                )

    # Generate Social Media Data
    for region, _, _ in REGIONS:
        social_media_rows.append({
            "date": date, "upazila": region,
            "hashtag_dengue":      random.randint(0, 5)  + get_social_media_spike(region, "Dengue", day),
            "hashtag_diarrhea":    random.randint(0, 5)  + get_social_media_spike(region, "Cholera", day),
            "hashtag_fever":       random.randint(10, 20) + get_social_media_spike(region, "Dengue", day),
            "hashtag_respiratory": random.randint(0, 5)  + get_social_media_spike(region, "Flu/Respiratory", day),
            "hashtag_allergy":     random.randint(0, 3),
        })

# ── 5. Write to CSV ───────────────────────────────────────────────────────────
def write_csv(filename, fieldnames, data):
    path = os.path.join(OUT, filename)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)

write_csv("facilities.csv", ["facility_id","name","upazila","lat","lon"], FACILITIES)
write_csv("sales.csv", ["date","facility_id","medicine_name","quantity_sold","upazila"], sales_rows)
write_csv("medicines.csv", ["medicine_name","base_daily_sales","outbreak_multiplier","signals_disease"], 
          [{"medicine_name": n, "base_daily_sales": b, "outbreak_multiplier": m, "signals_disease": d} for (n, b, m, d) in MEDICINES])
write_csv("outbreaks_ground_truth.csv", ["disease","start_day","end_day","upazilas","medicines"], 
          [{"disease": ob["disease"], "start_day": ob["start_day"], "end_day": ob["end_day"], "upazilas": "|".join(ob["upazilas"]), "medicines": "|".join(ob["medicines"])} for ob in OUTBREAKS])
write_csv("social_media_hashtags.csv",
          ["date", "upazila", "hashtag_dengue", "hashtag_diarrhea",
           "hashtag_fever", "hashtag_respiratory", "hashtag_allergy"],
          social_media_rows)

print("✓ Data generation complete (facilities, sales, medicines, outbreaks, social_media).")

