# ---------- IMPORTS ----------
import json                       # write FHIR as JSON files
import shutil                     # copy bad files to the errors folder
from pathlib import Path          # easy, safe file paths
from datetime import datetime     # convert HL7 dates

# ---------- FOLDERS ----------
INBOX = Path("data/hl7_inbox")    # Mirth writes HL7 files here
OUTBOX = Path("data/fhir_out")    # we write FHIR JSON here
ERRORS = Path("data/errors")      # files that fail go here



# ---------- STEP 2: READ AND SPLIT AN HL7 FILE ----------
def read_hl7(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    text = text.replace("\r\n", "\r").replace("\n", "\r")   # make every line ending \r

    segments = {}
    for line in text.split("\r"):          # one line = one segment
        if not line.strip():               # skip empty lines
            continue
        fields = line.split("|")           # split the segment into fields
        segments[fields[0]] = fields       # store by name: segments["PID"] = [...]
    return segments


# ---------- STEP 3: SAFE FIELD GETTER ----------
def get(segments, seg, field, comp=None):
    """get(segs, "PID", 5, 1) means PID-5.1 (last name). Returns None if missing."""
    fields = segments.get(seg)
    if fields is None:                     # segment not in this message
        return None

    index = field - 1 if seg == "MSH" else field   # THE MSH TRAP
    if index >= len(fields):               # field not there
        return None

    value = fields[index]
    if comp is not None:                   # we want one part, like PID-5.1
        parts = value.split("^")
        if comp > len(parts):
            return None
        value = parts[comp - 1]            # HL7 counts parts from 1, Python from 0

    value = value.strip()
    return value if value else None        # empty text becomes None

# ---------- STEP 4: HL7 DATE -> ISO DATE ----------
def to_iso(value):
    """20261107113000 -> 2026-11-07T11:30:00   and   19880924 -> 1988-09-24"""
    if value is None:
        return None
    if len(value) >= 14:                                   # date + time
        return datetime.strptime(value[:14], "%Y%m%d%H%M%S").isoformat()
    if len(value) == 8:                                    # date only
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    return None


# ---------- STEP 5: FHIR PATIENT (from PID) ----------
GENDER = {"M": "male", "F": "female"}

def build_patient(segs):
    mrn = get(segs, "PID", 3, 1)
    return {
        "resourceType": "Patient",
        "id": mrn,
        "identifier": [{"system": "urn:mercy-hosp:mrn", "value": mrn}],
        "name": [{
            "family": get(segs, "PID", 5, 1),
            "given": [get(segs, "PID", 5, 2)],
        }],
        "gender": GENDER.get(get(segs, "PID", 8), "unknown"),
        "birthDate": to_iso(get(segs, "PID", 7)),
        "address": [{
            "line": [get(segs, "PID", 11, 1)],
            "city": get(segs, "PID", 11, 3),
            "state": get(segs, "PID", 11, 4),
            "postalCode": get(segs, "PID", 11, 5),
        }],
    }

# ---------- STEP 6: FHIR ENCOUNTER (from PV1) ----------
CLASS_CODES = {"I": "IMP", "E": "EMER", "O": "AMB"}     # inpatient / emergency / outpatient
PRIORITY_CODES = {"E": "EM", "U": "UR", "R": "R"}       # emergency / urgent / routine
STATUS_BY_EVENT = {"A01": "in-progress", "A03": "finished"}

def build_encounter(segs):
    mrn = get(segs, "PID", 3, 1)
    event = get(segs, "MSH", 9, 2)
    visit_id = get(segs, "PV1", 19)
    unit = get(segs, "PV1", 3, 1)
    room = get(segs, "PV1", 3, 2)
    bed = get(segs, "PV1", 3, 3)

    encounter = {
        "resourceType": "Encounter",
        "id": visit_id,
        "identifier": [{"system": "urn:mercy-hosp:visit", "value": visit_id}],
        "status": STATUS_BY_EVENT.get(event, "unknown"),
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": CLASS_CODES.get(get(segs, "PV1", 2), "IMP"),
        },
        "priority": {"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActPriority",
            "code": PRIORITY_CODES.get(get(segs, "PV1", 4), "R"),
        }]},
        "subject": {"reference": f"Patient/{mrn}"},
        "period": {"start": to_iso(get(segs, "PV1", 44))},
        "location": [{"location": {"display": f"{unit}-{room}-{bed}"}}],
    }

    discharge = to_iso(get(segs, "PV1", 45))     # only A03 has this
    if discharge:
        encounter["period"]["end"] = discharge

    doc_last = get(segs, "PV1", 7, 2)            # blank doctor -> leave it out
    if doc_last:
        doc_first = get(segs, "PV1", 7, 3)
        encounter["participant"] = [{"individual": {"display": f"{doc_last}, {doc_first}"}}]

    return encounter



# ---------- STEP 7: BUNDLE + SAVE ONE FILE ----------
def build_bundle(segs):
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "identifier": {"system": "urn:mercy-hosp:message-id", "value": get(segs, "MSH", 10)},
        "timestamp": to_iso(get(segs, "MSH", 7)),
        "entry": [
            {"resource": build_patient(segs)},      # ALWAYS entry[0] = Patient
            {"resource": build_encounter(segs)},    # ALWAYS entry[1] = Encounter
        ],
    }


def convert_file(path, out_path):
    segs = read_hl7(path)
    for needed in ("MSH", "PID", "PV1"):            # basic sanity check
        if needed not in segs:
            raise ValueError(f"missing {needed} segment")

    bundle = build_bundle(segs)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2)

# ---------- STEP 8: CONVERT EVERYTHING ----------
def main():
    OUTBOX.mkdir(parents=True, exist_ok=True)       # create folders if missing
    ERRORS.mkdir(parents=True, exist_ok=True)

    files = sorted(INBOX.glob("*.hl7"))
    converted = skipped = errored = 0

    for path in files:
        out_path = OUTBOX / (path.stem + ".json")   # 1000.hl7 -> 1000.json
        if out_path.exists():                       # already done -> skip
            skipped += 1
            continue
        try:
            convert_file(path, out_path)
            converted += 1
        except Exception as e:                      # one bad file never stops the rest
            shutil.copy(path, ERRORS / path.name)
            print(f"ERROR in {path.name}: {e}")
            errored += 1

    print(f"Files found: {len(files)}   Converted: {converted}   "
          f"Skipped: {skipped}   Errors: {errored}")


# ---------- RUN ----------
if __name__ == "__main__":
    main()



