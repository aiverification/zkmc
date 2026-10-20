"""Count places/transitions/arcs of every P/T MCC instance; join with oracle verdicts."""
import csv, glob, os, re, sys, tarfile
from multiprocessing import Pool
sys.set_int_max_str_digits(0)
INPUTS, ORACLE, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
PAT = {k: re.compile(rb"<%s[\s>]" % k.encode()) for k in ("place", "transition", "arc")}
INSC = re.compile(rb"<inscription>\s*<text>\s*(\d+)")

def sizes(tgz):
    name = os.path.basename(tgz)[:-4]
    counts = {k: 0 for k in PAT}; max_w = 1; tail = b""
    with tarfile.open(tgz) as tf:
        m = next(x for x in tf.getmembers() if x.name.endswith("/model.pnml"))
        fh = tf.extractfile(m)
        while chunk := fh.read(1 << 22):
            buf = tail + chunk
            cut = max(0, len(buf) - 64)
            for k, p in PAT.items():
                counts[k] += len(p.findall(buf, 0, cut))
            for w in INSC.findall(buf, 0, cut):
                max_w = max(max_w, int(w))
            tail = buf[cut:]
        for k, p in PAT.items():
            counts[k] += len(p.findall(tail))
    return name, counts, max_w

def verdicts(path):
    try: return re.findall(r"^FORMULA \S+ (\S+)", open(path).read(), re.M)
    except FileNotFoundError: return []

def states(name):
    try: txt = open(os.path.join(ORACLE, name + "-SS.out")).read()
    except FileNotFoundError: return "", ""
    s = re.search(r"STATES (\d+)", txt); b = re.search(r"MAX_TOKEN_IN_PLACE (\d+)", txt)
    return (len(s.group(1)) if s else ""), (b.group(1) if b else "")  # digits of state count, max tokens

if __name__ == "__main__":
    tgzs = sorted(t for t in glob.glob(os.path.join(INPUTS, "*-PT-*.tgz")))
    with Pool(4) as pool:
        res = pool.map(sizes, tgzs, chunksize=4)
    with open(OUT, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["instance", "model", "places", "transitions", "arcs", "max_arc_weight",
                    "deadlock", "state_digits", "max_tokens", "ltl_total", "ltl_true"])
        for name, c, mw in res:
            rd = verdicts(os.path.join(ORACLE, name + "-RD.out"))
            ltl = verdicts(os.path.join(ORACLE, name + "-LTLC.out")) + verdicts(os.path.join(ORACLE, name + "-LTLF.out"))
            sd, mt = states(name)
            w.writerow([name, name.split("-")[0], c["place"], c["transition"], c["arc"], mw,
                        rd[0] if rd else "", sd, mt, len(ltl), ltl.count("TRUE")])
    print("wrote", OUT, len(res))
