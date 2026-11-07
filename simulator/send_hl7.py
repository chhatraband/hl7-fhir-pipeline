# ---------- IMPORTS: tools we borrow ----------
import random
import argparse                              
from datetime import datetime, timedelta     

from faker import Faker
from hl7.client import MLLPClient            

# ---------- MAKE RESULTS REPEATABLE ----------
random.seed(42)
Faker.seed(42)
fake = Faker("en_US")

# ---------- SETTINGS ----------
NUM_PATIENTS = 300
DAYS_OF_HISTORY = 90
MIN_STAY_HOURS = 6
MAX_STAY_HOURS = 240
READMIT_RATE = 0.15
DUPLICATE_RATE = 0.05
BLANK_DOCTOR_RATE = 0.08

MIRTH_HOST = "localhost"
MIRTH_PORT = 6661

# ---------- REFERENCE LISTS ----------
UNITS = ["ICU", "ED", "MED-SURG", "CARDIOLOGY", "ONCOLOGY", "NEUROLOGY"]

DOCTORS = [
    ("DR001", "Williams", "Sarah"),
    ("DR002", "Patel", "Rajan"),
    ("DR003", "Chen", "Michael"),
    ("DR004", "Johnson", "Lisa"),
    ("DR005", "Garcia", "Maria"),
    ("DR006", "Okafor", "David"),
]

ADMIT_TYPES = ["E", "U", "R"]

ZIP_CODES = ["07302", "07304", "07305", "07306", "07307", "07310"]


# ---------- STEP 2: ONE FAKE PATIENT ----------
def make_patient(number):
    sex = random.choice(["M", "F"])
    if sex == "M":
        first = fake.first_name_male()
    else:
        first = fake.first_name_female()

    return {
        "mrn": f"MRN{10000 + number}",
        "first": first,
        "last": fake.last_name(),
        "dob": fake.date_of_birth(minimum_age=18, maximum_age=90),
        "sex": sex,
        "street": fake.street_address(),
        "city": "Jersey City",
        "state": "NJ",                         # FIX 3
        "zip": random.choice(ZIP_CODES),
    }


# ---------- STEP 3: HL7 DATE FORMATS ----------        FIX 2
def hl7_time(dt):
    return dt.strftime("%Y%m%d%H%M%S")    # 2026-11-07 11:30:00  ->  20261107113000


def hl7_date(d):
    return d.strftime("%Y%m%d")           # 1988-09-24  ->  19880924


# ---------- STEP 4: MSH, EVN, PID LINES ----------
def build_msh(event, msg_id, msg_time):
    return "|".join([
        "MSH",
        "^~\\&",                        # MSH-2: the special characters HL7 uses
        "SIM_EPIC",                     # MSH-3: sending app (our fake Epic)
        "MERCY_HOSP",                   # MSH-4: sending hospital
        "MIRTH",                        # MSH-5: receiving app
        "DATA_LAKE",                    # MSH-6: receiving facility
        hl7_time(msg_time),             # MSH-7: when the message was made
        "",                             # MSH-8: security (empty)
        f"ADT^{event}^ADT_{event}",     # MSH-9: message type, e.g. ADT^A01
        msg_id,                         # MSH-10: unique message ID
        "P",                            # MSH-11: P = production
        "2.5",                          # MSH-12: HL7 version
    ])


def build_evn(event, msg_time):
    return f"EVN|{event}|{hl7_time(msg_time)}"


def build_pid(p):
    fields = [""] * 12                  # 12 empty boxes: PID-0 to PID-11
    fields[0] = "PID"
    fields[1] = "1"
    fields[3] = f"{p['mrn']}^^^MERCY^MR"                                  # PID-3: MRN
    fields[5] = f"{p['last']}^{p['first']}"                               # PID-5: name
    fields[7] = hl7_date(p["dob"])                                        # PID-7: birth date
    fields[8] = p["sex"]                                                  # PID-8: sex
    fields[11] = f"{p['street']}^^{p['city']}^{p['state']}^{p['zip']}"    # PID-11: address
    return "|".join(fields)


# ---------- STEP 5: PV1 LINE (THE VISIT) ----------
def build_pv1(visit, event):
    fields = [""] * 46                  # 46 boxes: PV1-0 to PV1-45
    fields[0] = "PV1"
    fields[1] = "1"
    fields[2] = "I"                     # PV1-2: I = inpatient
    fields[3] = f"{visit['unit']}^{visit['room']}^{visit['bed']}"   # PV1-3: unit^room^bed
    fields[4] = visit["admit_type"]     # PV1-4: E / U / R

    if visit["doctor"] is not None:     # PV1-7: doctor, only if we have one
        doc_id, doc_last, doc_first = visit["doctor"]
        fields[7] = f"{doc_id}^{doc_last}^{doc_first}"

    fields[19] = visit["visit_id"]                   # PV1-19: visit number
    fields[44] = hl7_time(visit["admit_time"])       # PV1-44: admit time

    if event == "A03":                               # PV1-45: discharge time, A03 only
        fields[45] = hl7_time(visit["discharge_time"])

    return "|".join(fields)


# ---------- STEP 6: THE WHOLE MESSAGE ----------
def build_message(event, patient, visit, msg_id, msg_time):
    segments = [
        build_msh(event, msg_id, msg_time),
        build_evn(event, msg_time),
        build_pid(patient),
        build_pv1(visit, event),
    ]
    return "\r".join(segments)          # HL7 lines are separated by \r, NOT \n



# ---------- STEP 7: THE HOSPITAL TIMELINE ----------
def random_admit_time(now):
    start = now - timedelta(days=DAYS_OF_HISTORY)                # 90 days ago
    seconds = random.randint(0, DAYS_OF_HISTORY * 24 * 60 * 60)  # random moment in those 90 days
    return (start + timedelta(seconds=seconds)).replace(second=0, microsecond=0)


def make_visit(visit_number, admit_time):
    stay_hours = random.randint(MIN_STAY_HOURS, MAX_STAY_HOURS)

    if random.random() < BLANK_DOCTOR_RATE:     # 8% of the time: no doctor
        doctor = None
    else:
        doctor = random.choice(DOCTORS)

    return {
        "visit_id": f"V{20000 + visit_number}",
        "unit": random.choice(UNITS),
        "room": str(random.randint(100, 499)),
        "bed": random.choice(["A", "B"]),
        "admit_type": random.choice(ADMIT_TYPES),
        "doctor": doctor,
        "admit_time": admit_time,
        "discharge_time": admit_time + timedelta(hours=stay_hours),
    }


def build_timeline(now):
    patients = []
    visits = []                                  # list of (patient, visit) pairs

    for i in range(1, NUM_PATIENTS + 1):         # 1, 2, 3 ... 300
        patient = make_patient(i)
        patients.append(patient)

        first_visit = make_visit(len(visits) + 1, random_admit_time(now))
        visits.append((patient, first_visit))

        if random.random() < READMIT_RATE:       # 15% come back
            gap = timedelta(days=random.randint(3, 29), hours=random.randint(0, 23))
            second_admit = first_visit["discharge_time"] + gap
            if second_admit < now:               # can't admit someone in the future
                second_visit = make_visit(len(visits) + 1, second_admit)
                visits.append((patient, second_visit))

    return patients, visits


# ---------- STEP 8: EVENTS IN TIME ORDER ----------
def make_events(visits, now):
    events = []
    for patient, visit in visits:
        events.append(("A01", visit["admit_time"], patient, visit))         # admission
        if visit["discharge_time"] <= now:                                  # already left?
            events.append(("A03", visit["discharge_time"], patient, visit)) # discharge

    events.sort(key=lambda e: e[1])          # oldest first, like a real day at the hospital

    messages = []
    for n, (event, when, patient, visit) in enumerate(events, start=1):
        msg_id = f"MSG{n:06d}"               # MSG000001, MSG000002, ...
        text = build_message(event, patient, visit, msg_id, when)
        messages.append({"event": event, "msg_id": msg_id, "text": text})

    return messages

# ---------- STEP 9: THE --dry-run SWITCH ----------
def parse_args():
    parser = argparse.ArgumentParser(description="Send fake HL7 ADT messages to Mirth")
    parser.add_argument("--dry-run", action="store_true",
                        help="print 3 messages instead of sending them")
    return parser.parse_args()


# ---------- STEP 10: SEND TO MIRTH (WITH DUPLICATES) ----------
def send_all(messages):
    sent = 0
    rejected = 0

    with MLLPClient(MIRTH_HOST, MIRTH_PORT) as client:    # open the connection once
        for m in messages:
            copies = 2 if m["send_twice"] else 1           # duplicates are sent twice
            for _ in range(copies):
                ack = client.send_message(m["text"])
                sent += 1

                if isinstance(ack, bytes):                 # the reply may come back as bytes
                    ack = ack.decode(errors="ignore")
                if "MSA|AA" not in ack:                    # AA = Application Accept
                    rejected += 1
                    print("Not accepted:", m["msg_id"])

            if sent % 100 < copies:                        # progress every ~100
                print(f"  sent {sent} ...")

    print(f"Done. Sent {sent} messages, {rejected} not accepted.")


# ---------- STEP 11: SUMMARY (YOUR ANSWER KEY) ----------
def print_summary(patients, visits, messages):
    a01 = sum(1 for m in messages if m["event"] == "A01")
    a03 = sum(1 for m in messages if m["event"] == "A03")
    dups = sum(1 for m in messages if m["send_twice"])
    blanks = sum(1 for _, v in visits if v["doctor"] is None)

    lines = [
        "========== ANSWER KEY ==========",
        f"Patients:            {len(patients)}",
        f"Visits (stays):      {len(visits)}",
        f"Readmissions:        {len(visits) - len(patients)}",
        f"A01 admits:          {a01}",
        f"A03 discharges:      {a03}",
        f"Still in hospital:   {a01 - a03}",
        f"Blank doctors:       {blanks}",
        f"Unique messages:     {len(messages)}",
        f"Duplicate resends:   {dups}",
        f"Total sent to Mirth: {len(messages) + dups}",
        "================================",
    ]
    print("\n".join(lines))

    with open("data/answer_key.txt", "w") as f:       # keep a copy for Phases 5 and 6
        f.write("\n".join(lines))

# ---------- RUN ----------

if __name__ == "__main__":
    args = parse_args()
    now = datetime.now().replace(second=0, microsecond=0)

    patients, visits = build_timeline(now)
    messages = make_events(visits, now)

    for m in messages:                                  # decide duplicates up front
        m["send_twice"] = random.random() < DUPLICATE_RATE

    if args.dry_run:
        print("DRY RUN: showing the first 3 messages, nothing is sent\n")
        for m in messages[:3]:
            print(m["text"].replace("\r", "\n"))
            print()
    else:
        send_all(messages)

    print_summary(patients, visits, messages)