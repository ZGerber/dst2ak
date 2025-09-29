# test_stpln_reader.py
import struct
from pathlib import Path
from dst2ak.blockreader import BlockReader
from dst2ak.eventassembler import EventAssembler


def parse_stpln(bank):
    """Parse stpln_dst_common from a Bank object using C pack/unpack logic."""
    data = memoryview(bank.data[8:])  # skip bank header
    offset = 0

    def read(fmt, n=1, as_list=True):
        nonlocal offset
        size = struct.calcsize(fmt)
        buf = data[offset:offset + size * n]
        offset += size * n
        vals = struct.unpack(f"<{n}{fmt[-1]}", buf)
        if n == 1 and not as_list:
            return vals[0]
        return list(vals)

    out = {}

    # --- scalars ---
    out["jday"], out["jsec"], out["msec"] = read("<i", 3, as_list=True)

    # neye, nmir, ntube (packed as shorts)
    out["neye"], out["nmir"], out["ntube"] = read("<h", 3, as_list=True)

    # maxeye and if_eye[maxeye]
    out["maxeye"] = read("<i", 1, as_list=False)
    out["if_eye"] = read("<i", out["maxeye"])

    # --- per-eye data (conditional) ---
    eyes = []
    for ieye in range(out["maxeye"]):
        if out["if_eye"][ieye] != 1:
            eyes.append(None)
            continue

        eye = {}
        eye["eyeid"]     = read("<h", 1, as_list=False)
        eye["eye_nmir"]  = read("<h", 1, as_list=False)
        eye["eye_ngmir"] = read("<h", 1, as_list=False)
        eye["eye_ntube"] = read("<h", 1, as_list=False)
        eye["eye_ngtube"]= read("<h", 1, as_list=False)

        eye["rmsdevpln"]   = read("<f", 1, as_list=False)
        eye["rmsdevtim"]   = read("<f", 1, as_list=False)
        eye["tracklength"] = read("<f", 1, as_list=False)
        eye["crossingtime"]= read("<f", 1, as_list=False)
        eye["ph_per_gtube"]= read("<f", 1, as_list=False)

        eye["n_ampwt"]     = read("<f", 3)
        eye["errn_ampwt"]  = read("<f", 6)
        eyes.append(eye)
    out["eyes"] = eyes

    # --- mirror info ---
    mirrors = []
    for _ in range(out["nmir"]):
        mir = {}
        mir["mirid"]      = read("<h", 1, as_list=False)
        mir["mir_eye"]    = read("<h", 1, as_list=False)
        mir["mir_type"]   = read("<h", 1, as_list=False)
        mir["mir_ngtube"] = read("<i", 1, as_list=False)
        mir["mirtime_ns"] = read("<i", 1, as_list=False)
        mirrors.append(mir)
    out["mirrors"] = mirrors

    # --- tube info ---
    tubes = []
    for _ in range(out["ntube"]):
        tube = {}
        tube["ig"]       = read("<h", 1, as_list=False)
        tube["tube_eye"] = read("<h", 1, as_list=False)
        tubes.append(tube)

    # version-dependent
    if bank.bank_version >= 2:
        for t in tubes:
            t["saturated"]   = read("<i", 1, as_list=False)
        for t in tubes:
            t["mir_tube_id"] = read("<i", 1, as_list=False)
    out["tubes"] = tubes

    return out


if __name__ == "__main__":
    fname = "/media/zane/ta_storage_4/data/hybrid/tmatched_hybrid_only/MDSD_230915.tmatch.dst.gz"
    toml_path = "/home/zane/software/dst2ak/config/containers.toml"

    with BlockReader(fname) as br:
        ea = EventAssembler(br, toml_path, keep_markers=False)
        for iev, event in enumerate(ea):
            print(f"\n=== Event {iev} ===")
            stpln_bank = next(b for b in event.banks if b.bank_id == 15043)
            parsed = parse_stpln(stpln_bank)

            print("jday:", parsed["jday"], "jsec:", parsed["jsec"], "msec:", parsed["msec"])
            print("neye:", parsed["neye"], "nmir:", parsed["nmir"], "ntube:", parsed["ntube"])
            print("if_eye:", parsed["if_eye"])
            for ie, eye in enumerate(parsed["eyes"]):
                if eye is None:
                    continue
                print(f"  Eye {ie}: n_ampwt={eye['n_ampwt']} rmsdevpln={eye['rmsdevpln']}")
            print("First 3 mirrors:", parsed["mirrors"][:3])
            print("First 5 tubes:", parsed["tubes"][:5])

            if iev >= 1:  # just 2 events for test
                break
