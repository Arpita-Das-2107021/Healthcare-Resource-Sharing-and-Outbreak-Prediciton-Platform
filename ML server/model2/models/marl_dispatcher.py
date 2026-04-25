"""
Phase 5 — Peer-to-Peer Inventory Request System
Greedy nearest-neighbor dispatch: alert pharmacies automatically request
required medicine from the closest available safe (Normal) pharmacy,
sorted by Haversine distance.
"""

import csv
import os
import math

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "../data")

# ── Config ────────────────────────────────────────────────────────────────────
DISEASE_TO_MEDICINE = {
    "Fever/Flu": "Paracetamol 500mg",
    "Diarrhea": "ORS Sachet",
    "Respiratory": "Amoxicillin 500mg",
    "Allergy/Fever": "Cetirizine 10mg"
}

BASE_RESERVE_STOCK = 150

# ── Data classes ──────────────────────────────────────────────────────────────
class SafePharmacy:
    def __init__(self, name, upazila, lat, lon):
        self.name    = name
        self.upazila = upazila
        self.lat     = float(lat)
        self.lon     = float(lon)
        self.stock   = {med: BASE_RESERVE_STOCK for med in DISEASE_TO_MEDICINE.values()}

    def fulfill_request(self, medicine, quantity):
        if self.stock.get(medicine, 0) >= quantity:
            self.stock[medicine] -= quantity
            return True
        return False

class AlertPharmacy:
    def __init__(self, name, upazila, lat, lon, medicine, demand):
        self.name       = name
        self.upazila    = upazila
        self.lat        = float(lat)
        self.lon        = float(lon)
        self.medicine   = medicine
        self.demand     = demand
        self.shortage   = demand
        self.filled     = 0

# ── Logistics Helpers ─────────────────────────────────────────────────────────
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# ── Main Runner ───────────────────────────────────────────────────────────────
def run_request_dispatcher():
    print("="*100)
    print("  MARL Dispatcher — Automated Pharmacy Inventory Requests")
    print("="*100)

    # 1. Load Pharmacies for coordinates and real names
    pharmacy_data = {}
    ph_path = os.path.join(DATA_DIR, "pharmacies.csv")
    fa_path = os.path.join(DATA_DIR, "facilities.csv")
    if os.path.exists(ph_path):
        with open(ph_path) as f:
            for row in csv.DictReader(f):
                pharmacy_data[row["pharmacy_id"]] = row
    elif os.path.exists(fa_path):
        with open(fa_path) as f:
            for row in csv.DictReader(f):
                pharmacy_data[row["facility_id"]] = row

    # 2. Load Predictions & Split into Safe vs Alert
    safe_suppliers = []
    alert_needs    = []

    pred_path = os.path.join(DATA_DIR, "predictions.csv")
    if not os.path.exists(pred_path):
        print("  No predictions.csv found. Run ST-GNN first!")
        return

    with open(pred_path) as f:
        for row in csv.DictReader(f):
            # Accept either pharmacy_id or facility_id column in predictions
            ph_id = row.get("pharmacy_id") or row.get("facility_id", "")
            upazila = row.get("upazila", "")
            ph = pharmacy_data.get(ph_id, {})
            name = ph.get("name", ph_id)
            lat  = ph.get("lat", 0)
            lon  = ph.get("lon", 0)

            if row.get("status") == "ALERT":
                disease = row.get("likely_disease", "Fever/Flu")
                med = DISEASE_TO_MEDICINE.get(disease, "Paracetamol 500mg")
                confidence = float(row.get("final_confidence", 0.8))
                demand = int(200 * confidence)
                alert_needs.append(AlertPharmacy(name, upazila, lat, lon, med, demand))
            elif row.get("status") == "Normal":
                safe_suppliers.append(SafePharmacy(name, upazila, lat, lon))

    if not alert_needs:
        print("\n  No outbreaks detected nationwide. No requests generated.")
        return

    # 3. Request Generation Algorithm
    inventory_requests = []

    for need in alert_needs:
        safe_suppliers.sort(key=lambda s: haversine_km(s.lat, s.lon, need.lat, need.lon))

        for safe_ph in safe_suppliers:
            if need.shortage <= 0: break

            available_stock = safe_ph.stock.get(need.medicine, 0)
            if available_stock > 0:
                transfer_qty = min(need.shortage, available_stock)

                safe_ph.fulfill_request(need.medicine, transfer_qty)
                need.filled += transfer_qty
                need.shortage -= transfer_qty

                dist_km = haversine_km(safe_ph.lat, safe_ph.lon, need.lat, need.lon)

                inventory_requests.append({
                    "requesting_pharmacy": need.name,
                    "requesting_region": need.upazila,
                    "supplying_pharmacy": safe_ph.name,
                    "supplying_region": safe_ph.upazila,
                    "requested_medicine": need.medicine,
                    "quantity": transfer_qty,
                    "distance_km": round(dist_km, 2)
                })

    # 4. Print Output
    print(f"  {'Requesting Pharmacy':<25} | {'Closest Safe Supplier':<25} | {'Medicine Requested':<20} | {'Qty':>4} | {'Dist (km)':>9}")
    print("  " + "─"*95)
    for req in inventory_requests:
        print(f"  {req['requesting_pharmacy'][:25]:<25} | {req['supplying_pharmacy'][:25]:<25} | {req['requested_medicine']:<20} | {req['quantity']:>4} | {req['distance_km']:>9}")

    # 5. Save to CSV
    request_path = os.path.join(DATA_DIR, "inventory_requests.csv")
    with open(request_path, "w", newline="") as f:
        fieldnames = ["requesting_pharmacy", "requesting_region", "supplying_pharmacy", "supplying_region", "requested_medicine", "quantity", "distance_km"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(inventory_requests)

    # 6. Fulfillment summary
    total_demand   = sum(n.demand   for n in alert_needs)
    total_filled   = sum(n.filled   for n in alert_needs)
    coverage_pct   = 100 * total_filled / max(total_demand, 1)
    unfulfilled    = [n for n in alert_needs if n.shortage > 0]

    print(f"\n  Supply coverage: {total_filled}/{total_demand} units ({coverage_pct:.1f}%)")
    if unfulfilled:
        print(f"  {len(unfulfilled)} pharmacy(s) have unmet demand:")
        for n in unfulfilled:
            print(f"    {n.name}: {n.shortage} units of {n.medicine} still needed")

    print(f"\n  Inventory requests saved → {request_path}")

if __name__ == "__main__":
    run_request_dispatcher()
