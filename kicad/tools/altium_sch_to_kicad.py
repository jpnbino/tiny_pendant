#!/usr/bin/env python3
"""One-shot converter: Altium SchLib/SchDoc (frozen in ../../altium) -> KiCad symbol library and schematics.

KiCad has no CLI importer for Altium schematics, so this parses the Altium binary
records (needs `pip install olefile`) and writes KiCad 9 s-expression files; run
`kicad-cli sch upgrade --force` afterwards to save them in the installed KiCad format.

Usage: python3 altium_sch_to_kicad.py   (paths are relative to this file)
"""
import os
import re
import struct
import uuid

import olefile

HERE = os.path.dirname(os.path.abspath(__file__))
ALT = os.path.join(HERE, "..", "..", "altium")
OUT = os.path.join(HERE, "..")
NS = uuid.UUID("6f0e1c1e-3b0a-4f4e-9d1e-7a1f0c5b2d11")
PROJECT = "tiny_pendant"
LIB = "tiny_pendant"
FP_LIB = "tiny_pendant"
U = 0.254  # Altium schematic unit (10 mil) in mm

FILES = [("TINY_PENDANT.SchDoc", "tiny_pendant", "TINY PENDANT"),
         ("[01] - CAPA.SchDoc", "01_CAPA", "CAPA"),
         ("[02] - DIAGRAMA DE BLOCOS.SchDoc", "02_DIAGRAMA_DE_BLOCOS", "DIAGRAMA DE BLOCOS"),
         ("[03] - CIRCUITO.SchDoc", "03_CIRCUITO", "CIRCUITO"),
         ("[04] - MECANICO.SchDoc", "04_MECANICO", "MECANICO"),
         ("[05] - DOC HISTORICO REVISAO.SchDoc", "05_DOC_HISTORICO_REVISAO", "DOC HISTORICO REVISAO")]
SHEET_PAGES = {stem: i + 1 for i, (_, stem, _) in enumerate(FILES)}


def uid(*parts):
    return str(uuid.uuid5(NS, "/".join(str(p) for p in parts)))


ROOT_UUID = uid("root")


def n(v):
    s = ("%.4f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def parse_props(b):
    r = {}
    for kv in b.rstrip(b"\0").decode("latin1").split("|"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            r[k.upper()] = v
    if "%UTF8%TEXT" in r:
        r["TEXT"] = r["%UTF8%TEXT"].encode("latin1").decode("utf8", "replace")
    return r


def parse_pin(b):
    """Binary pin record found in SchLib streams."""
    p = 4 + 1 + 2 + 1 + 4
    p += 1 + b[p]  # description
    elec, cong = b[p + 1], b[p + 2]
    length, x, y = struct.unpack("<hhh", b[p + 3:p + 9])
    p += 9 + 4
    nl = b[p]
    name = b[p + 1:p + 1 + nl].decode("latin1")
    p += 1 + nl
    dl = b[p]
    des = b[p + 1:p + 1 + dl].decode("latin1")
    return {"RECORD": "2", "NAME": name, "DESIGNATOR": des, "ELECTRICAL": str(elec),
            "PINCONGLOMERATE": str(cong), "PINLENGTH": str(length),
            "LOCATION.X": str(x), "LOCATION.Y": str(y)}


def read_records(data):
    i, out = 0, []
    while i + 4 <= len(data):
        ln = struct.unpack("<I", data[i:i + 4])[0]
        kind, ln = ln >> 24, ln & 0xFFFFFF
        i += 4
        b = data[i:i + ln]
        i += ln
        out.append(parse_pin(b) if kind == 1 else parse_props(b))
    return out


def fi(r, k, d=0):
    try:
        return int(r.get(k, d))
    except ValueError:
        return d


ELEC = {"0": "input", "1": "bidirectional", "2": "output", "3": "open_collector", "4": "passive",
        "5": "tri_state", "6": "open_emitter", "7": "power_in"}
# Altium pin orientation (body -> hot end): 0 right, 1 up, 2 left, 3 down; KiCad angle points hot end -> body
PIN_ANGLE = {0: 180, 1: 270, 2: 0, 3: 90}
STROKE = "(stroke (width 0.1524) (type default))"
FONT = "(font (size 1.27 1.27))"


# ------------------------------------------------------------------ symbol library
def sym_graphics(children):
    g, pins = [], []
    for r in children:
        t = r.get("RECORD")
        if t in ("6", "7"):
            cnt = fi(r, "LOCATIONCOUNT")
            pts = [(fi(r, "X%d" % k) * U, fi(r, "Y%d" % k) * U) for k in range(1, cnt + 1)]
            fill = "outline" if r.get("ISSOLID") == "T" and t == "7" else "none"
            g.append("(polyline (pts %s) %s (fill (type %s)))" % (
                " ".join("(xy %s %s)" % (n(x), n(y)) for x, y in pts), STROKE, fill))
        elif t == "13":
            pts = [(fi(r, "LOCATION.X"), fi(r, "LOCATION.Y")), (fi(r, "CORNER.X"), fi(r, "CORNER.Y"))]
            g.append("(polyline (pts %s) %s (fill (type none)))" % (
                " ".join("(xy %s %s)" % (n(x * U), n(y * U)) for x, y in pts), STROKE))
        elif t == "14":
            fill = "background" if r.get("ISSOLID") == "T" else "none"
            g.append("(rectangle (start %s %s) (end %s %s) %s (fill (type %s)))" % (
                n(fi(r, "LOCATION.X") * U), n(fi(r, "LOCATION.Y") * U),
                n(fi(r, "CORNER.X") * U), n(fi(r, "CORNER.Y") * U), STROKE, fill))
        elif t in ("8", "12"):
            rad = (fi(r, "RADIUS") + fi(r, "RADIUS_FRAC") / 1e5) * U
            fill = "background" if r.get("ISSOLID") == "T" else "none"
            g.append("(circle (center %s %s) (radius %s) %s (fill (type %s)))" % (
                n(fi(r, "LOCATION.X") * U), n(fi(r, "LOCATION.Y") * U), n(rad), STROKE, fill))
        elif t == "4":
            g.append("(text %s (at %s %s 0) (effects %s (justify left bottom)))" % (
                q(r.get("TEXT", "")), n(fi(r, "LOCATION.X") * U), n(fi(r, "LOCATION.Y") * U), FONT))
        elif t == "2":
            cong = fi(r, "PINCONGLOMERATE")
            orient = cong & 3
            ln = fi(r, "PINLENGTH")
            dx, dy = [(1, 0), (0, 1), (-1, 0), (0, -1)][orient]
            hx = (fi(r, "LOCATION.X") + dx * ln) * U
            hy = (fi(r, "LOCATION.Y") + dy * ln) * U
            hide_name = "" if cong & 0x08 else " hide"
            hide_num = "" if cong & 0x10 else " hide"
            pins.append("(pin %s line (at %s %s %d) (length %s) (name %s (effects %s%s)) (number %s (effects %s%s)))" % (
                ELEC.get(r.get("ELECTRICAL", "4"), "passive"), n(hx), n(hy), PIN_ANGLE[orient],
                n(ln * U), q(r["NAME"]), FONT, hide_name, q(r["DESIGNATOR"]), FONT, hide_num))
    return g, pins


def build_symbol(libref, recs):
    comp, children = recs[0], recs[1:]
    g, pins = sym_graphics(children)
    des, comment, footprint, params = "U?", "", None, {}
    for r in children:
        t = r.get("RECORD")
        if t == "34" and r.get("NAME") == "Designator":
            des = r["TEXT"]
        elif t == "45" and r.get("MODELTYPE") == "PCBLIB":
            footprint = r["MODELNAME"]
        elif t == "41" and r.get("NAME") == "Comment":
            comment = r.get("TEXT", "")
        elif t == "41" and r.get("NAME") and not r["NAME"].startswith("."):
            params[r["NAME"]] = r.get("TEXT", "")
    value_key = next((k for k in params if k.lower() in ("value", "pad size")), None)
    value = params.get(value_key, libref) if value_key else libref
    if comment and not comment.startswith("="):
        value = comment
    props = [
        '(property "Reference" %s (at 0 3.81 0) (effects %s))' % (q(des.rstrip("?")), FONT),
        '(property "Value" %s (at 0 -3.81 0) (effects %s))' % (q(value), FONT),
        '(property "Footprint" %s (at 0 0 0) (effects %s hide))' % (
            q("%s:%s" % (FP_LIB, footprint) if footprint else ""), FONT),
        '(property "Datasheet" %s (at 0 0 0) (effects %s hide))' % (q(params.get("ComponentLink1URL", "")), FONT),
        '(property "Description" %s (at 0 0 0) (effects %s hide))' % (q(comp.get("COMPONENTDESCRIPTION", "")), FONT),
    ]
    skip = {"Value", "value", "ComponentLink1URL", "ComponentLink1Description", "Description",
            "Reference", "Footprint", "Datasheet"}
    props += ['(property %s %s (at 0 0 0) (effects %s hide))' % (q(k), q(v), FONT)
              for k, v in params.items() if k not in skip]
    text = ("  (symbol %s (pin_numbers (hide no)) (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board %s)\n"
            "    %s\n    (symbol %s\n%s\n%s\n    )\n  )" % (
                q(libref), "yes" if footprint else "no", "\n    ".join(props), q(libref + "_1_1"),
                "\n".join("      " + x for x in g), "\n".join("      " + p for p in pins)))
    return text, {"value": value, "footprint": footprint, "params": params, "recs": recs}


def power_symbol(name):
    return ('  (symbol %s (power) (pin_numbers (hide yes)) (pin_names (offset 0) hide) (exclude_from_sim no) (in_bom no) (on_board no)\n'
            '    (property "Reference" "#PWR" (at 0 -3.81 0) (effects %s hide))\n'
            '    (property "Value" %s (at 0 3.81 0) (effects %s))\n'
            '    (property "Footprint" "" (at 0 0 0) (effects %s hide))\n'
            '    (property "Datasheet" "" (at 0 0 0) (effects %s hide))\n'
            '    (property "Description" "Power port, bar style (converted from Altium)" (at 0 0 0) (effects %s hide))\n'
            '    (symbol %s\n'
            '      (polyline (pts (xy 0 0) (xy 0 1.27)) %s (fill (type none)))\n'
            '      (polyline (pts (xy -1.27 1.27) (xy 1.27 1.27)) %s (fill (type none)))\n'
            '      (pin power_in line (at 0 0 90) (length 0) (name %s (effects %s)) (number "1" (effects %s)))\n'
            '    )\n  )' % (q(name), FONT, q(name), FONT, FONT, FONT, FONT, q(name + "_1_1"),
                            STROKE, STROKE, q(name), FONT, FONT))


def pwr_flag_symbol():
    return ('  (symbol "PWR_FLAG" (power) (pin_numbers (hide yes)) (pin_names (offset 0) hide) (exclude_from_sim no) (in_bom no) (on_board no)\n'
            '    (property "Reference" "#FLG" (at 0 1.905 0) (effects %s hide))\n'
            '    (property "Value" "PWR_FLAG" (at 0 3.81 0) (effects %s))\n'
            '    (property "Footprint" "" (at 0 0 0) (effects %s hide))\n'
            '    (property "Datasheet" "~" (at 0 0 0) (effects %s hide))\n'
            '    (property "Description" "Marks a net as driven (silences ERC power_pin_not_driven)" (at 0 0 0) (effects %s hide))\n'
            '    (symbol "PWR_FLAG_1_1"\n'
            '      (polyline (pts (xy 0 0) (xy 0 1.27) (xy -1.016 1.905) (xy 0 2.54) (xy 1.016 1.905) (xy 0 1.27)) %s (fill (type none)))\n'
            '      (pin power_out line (at 0 0 90) (length 0) (name "pwr" (effects %s)) (number "1" (effects %s)))\n'
            '    )\n  )' % (FONT, FONT, FONT, FONT, FONT, STROKE, FONT, FONT))


def convert_lib():
    o = olefile.OleFileIO(os.path.join(ALT, "tiny_pendant.SchLib"))
    header = parse_props(o.openstream("FileHeader").read()[4:])
    names = sorted(header["LIBREF%d" % i] for i in range(int(header["COMPCOUNT"])))
    blocks, info = [], {}
    for nm in names:
        text, info[nm] = build_symbol(nm, read_records(o.openstream([nm, "Data"]).read()))
        blocks.append(text)
    blocks += [power_symbol(x) for x in ("GND", "VCC_5V")] + [pwr_flag_symbol()]
    text = ('(kicad_symbol_lib (version 20241209) (generator "altium_sch_to_kicad") (generator_version "1.0")\n'
            + "\n".join(blocks) + "\n)\n")
    with open(os.path.join(OUT, LIB + ".kicad_sym"), "w", encoding="utf8") as f:
        f.write(text)
    return info, text


# ------------------------------------------------------------------ schematic sheets
def embed_symbols(libs_text, names):
    blocks = []
    for nm in names:
        m = re.search(r"  \(symbol %s .*?\n  \)\n" % re.escape(q(nm)), libs_text, re.S)
        blocks.append(m.group(0).rstrip("\n").replace(
            "(symbol %s " % q(nm), "(symbol %s " % q("%s:%s" % (LIB, nm)), 1))
    return "\n".join(blocks)


VARS = {"projeto": "${PROJETO}", "variante": "${VARIANTE}", "versionrevision": "${VERSIONREVISION}",
        "currentdate": "${ISSUE_DATE}", "sheetnumber": "${#}", "sheettotal": "${##}", "documentname": "${TITLE}"}


def subst(txt):
    return VARS.get(txt[1:].lower(), txt) if txt.startswith("=") else txt


def sheet_uuid(stem):
    return ROOT_UUID if stem == PROJECT else uid("sheet", stem)


def stem_of(altium_name):
    return next(s for f, s, _ in FILES if f.lower() == altium_name.lower())


def convert_sheet(src, stem, title, info, libs_text):
    o = olefile.OleFileIO(os.path.join(ALT, src))
    recs = read_records(o.openstream("FileHeader").read())
    big = fi(recs[1], "CUSTOMX") > 1200
    H = 1170 if big else 820  # sheet height in Altium units, flips Y
    fonts = {k: (fi(recs[1], "SIZE%d" % k, 10), recs[1].get("BOLD%d" % k) == "T", recs[1].get("ITALIC%d" % k) == "T")
             for k in range(1, fi(recs[1], "FONTIDCOUNT") + 1)}
    sid = sheet_uuid(stem)

    def X(v):
        return v * U

    def Y(v):
        return (H - v) * U

    def eff(fontid, just=None):
        size, bold, ital = fonts.get(fontid, (10, False, False))
        h = min(round(size * U * 0.75, 2), 5.0)
        j = ""
        if just is not None:
            parts = [p for p in (["left", "", "right"][just % 3], ["bottom", "", "top"][just // 3]) if p]
            j = " (justify %s)" % " ".join(parts) if parts else ""
        return "(effects (font (size %s %s)%s%s)%s)" % (n(h), n(h), " bold" if bold else "", " italic" if ital else "", j)

    children = {}
    for r in recs:
        if "OWNERINDEX" in r:
            children.setdefault(int(r["OWNERINDEX"]) + 1, []).append(r)

    out, comp_names, pwr, flagged = [], [], 0, set()
    wires = [r for r in recs if r.get("RECORD") == "27"]
    for i, r in enumerate(recs):
        t = r.get("RECORD")
        key = (stem, i)
        if "OWNERINDEX" in r or i < 2:
            continue  # sheet template items / children handled by their owner
        if t == "4" and not r.get("TEXT"):
            continue
        if t == "4":
            out.append("(text %s (exclude_from_sim no) (at %s %s 0) %s (uuid %s))" % (
                q(subst(r.get("TEXT", ""))), n(X(fi(r, "LOCATION.X"))), n(Y(fi(r, "LOCATION.Y"))),
                eff(fi(r, "FONTID", 1), fi(r, "JUSTIFICATION", 0)), q(uid(*key))))
        elif t in ("6", "7"):
            pts = [(X(fi(r, "X%d" % k)), Y(fi(r, "Y%d" % k))) for k in range(1, fi(r, "LOCATIONCOUNT") + 1)]
            if t == "7" and pts and pts[0] != pts[-1]:
                pts.append(pts[0])
            fill = "outline" if t == "7" and r.get("ISSOLID") == "T" else "none"
            out.append("(polyline (pts %s) (stroke (width 0.1524) (type default)) (fill (type %s)) (uuid %s))" % (
                " ".join("(xy %s %s)" % (n(x), n(y)) for x, y in pts), fill, q(uid(*key))))
        elif t == "14":
            out.append("(rectangle (start %s %s) (end %s %s) (stroke (width 0.1524) (type default)) (fill (type none)) (uuid %s))" % (
                n(X(fi(r, "LOCATION.X"))), n(Y(fi(r, "LOCATION.Y"))), n(X(fi(r, "CORNER.X"))), n(Y(fi(r, "CORNER.Y"))),
                q(uid(*key))))
        elif t == "28":
            x1, y1, x2, y2 = X(fi(r, "LOCATION.X")), Y(fi(r, "LOCATION.Y")), X(fi(r, "CORNER.X")), Y(fi(r, "CORNER.Y"))
            out.append("(text_box %s (exclude_from_sim no) (at %s %s 0) (size %s %s) (margins 0.9525 0.9525 0.9525 0.9525) "
                       "(stroke (width 0) (type default)) (fill (type none)) %s (uuid %s))" % (
                           q(r.get("TEXT", "").replace("~1", "\n")), n(min(x1, x2)), n(min(y1, y2)),
                           n(abs(x2 - x1)), n(abs(y2 - y1)), eff(fi(r, "FONTID", 1), 6), q(uid(*key))))
        elif t == "27":
            pts = [(X(fi(r, "X%d" % k)), Y(fi(r, "Y%d" % k))) for k in range(1, fi(r, "LOCATIONCOUNT") + 1)]
            out.append("(wire (pts %s) (stroke (width 0) (type default)) (uuid %s))" % (
                " ".join("(xy %s %s)" % (n(x), n(y)) for x, y in pts), q(uid(*key))))
        elif t == "17":
            name, ang = r["TEXT"], ((fi(r, "ORIENTATION") - 1) * 90) % 360
            x, y = X(fi(r, "LOCATION.X")), Y(fi(r, "LOCATION.Y"))
            pwr += 1
            ref = "#PWR%02d" % pwr
            comp_names.append(name)
            out.append(
                '(symbol (lib_id %s) (at %s %s %d) (unit 1) (exclude_from_sim no) (in_bom no) (on_board no) (dnp no)\n'
                '  (uuid %s)\n'
                '  (property "Reference" %s (at %s %s 0) (effects (font (size 1.27 1.27)) hide))\n'
                '  (property "Value" %s (at %s %s 0) (effects (font (size 1.27 1.27))))\n'
                '  (pin "1" (uuid %s))\n'
                '  (instances (project %s (path "/%s/%s" (reference %s) (unit 1)))))' % (
                    q("%s:%s" % (LIB, name)), n(x), n(y), ang, q(uid(*key)), q(ref), n(x), n(y),
                    q(name), n(x + 1.27 if ang == 90 else x), n(y - 2.54 if ang in (0, 270) else y + 2.54),
                    q(uid(*key, "pin")), q(PROJECT), ROOT_UUID, sid, q(ref)))
            if name not in flagged:
                # the schematic has no power source symbol: flag the net so ERC knows it is driven
                flagged.add(name)
                loc = (fi(r, "LOCATION.X"), fi(r, "LOCATION.Y"))
                for w in wires:
                    ends = [(fi(w, "X1"), fi(w, "Y1")), (fi(w, "X2"), fi(w, "Y2"))]
                    if loc in ends:
                        fx, fy = X(ends[1 - ends.index(loc)][0]), Y(ends[1 - ends.index(loc)][1])
                        comp_names.append("PWR_FLAG")
                        out.append(
                            '(symbol (lib_id "%s:PWR_FLAG") (at %s %s 0) (unit 1) (exclude_from_sim no) (in_bom no) (on_board no) (dnp no)\n'
                            '  (uuid %s)\n'
                            '  (property "Reference" "#FLG0%d" (at %s %s 0) (effects (font (size 1.27 1.27)) hide))\n'
                            '  (property "Value" "PWR_FLAG" (at %s %s 0) (effects (font (size 1.27 1.27)) hide))\n'
                            '  (pin "1" (uuid %s))\n'
                            '  (instances (project %s (path "/%s/%s" (reference "#FLG0%d") (unit 1)))))' % (
                                LIB, n(fx), n(fy), q(uid(*key, "flag")), len(flagged), n(fx), n(fy), n(fx), n(fy),
                                q(uid(*key, "flagpin")), q(PROJECT), ROOT_UUID, sid, len(flagged)))
                        break
        elif t == "1":
            kids = children.get(i, [])
            libref = r["LIBREFERENCE"]
            comp_names.append(libref)
            desr = next(k for k in kids if k.get("RECORD") == "34" and k.get("NAME") == "Designator")
            des = desr["TEXT"]
            vals = {k["NAME"].lower(): k.get("TEXT", "") for k in kids if k.get("RECORD") == "41" and k.get("NAME")}
            comment = next((k for k in kids if k.get("RECORD") == "41" and k.get("NAME") == "Comment"), None)
            ctext = comment.get("TEXT", "") if comment else ""
            value = vals.get(ctext[1:].lower(), "") if ctext.startswith("=") else ctext
            value = value or info[libref]["value"]
            fp = info[libref]["footprint"]
            ang = {0: 0, 1: 90, 2: 180, 3: 270}[fi(r, "ORIENTATION")]
            mirror = " (mirror y)" if r.get("ISMIRRORED") == "T" else ""
            x, y = X(fi(r, "LOCATION.X")), Y(fi(r, "LOCATION.Y"))
            dx, dy = X(fi(desr, "LOCATION.X")), Y(fi(desr, "LOCATION.Y"))
            cx, cy = (X(fi(comment, "LOCATION.X")), Y(fi(comment, "LOCATION.Y"))) if comment else (x, y)
            hide_ref = " hide" if desr.get("ISHIDDEN") == "T" else ""
            hide_val = " hide" if (comment is None or comment.get("ISHIDDEN") == "T") else ""
            pin_xml = "\n".join("  (pin %s (uuid %s))" % (q(p["DESIGNATOR"]), q(uid(*key, "pin", p["DESIGNATOR"])))
                                for p in info[libref]["recs"] if p.get("RECORD") == "2")
            out.append(
                '(symbol (lib_id %s) (at %s %s %d)%s (unit 1) (exclude_from_sim no) (in_bom yes) (on_board %s) (dnp no)\n'
                '  (uuid %s)\n'
                '  (property "Reference" %s (at %s %s 0) (effects (font (size 1.27 1.27)) (justify left bottom)%s))\n'
                '  (property "Value" %s (at %s %s 0) (effects (font (size 1.27 1.27)) (justify left top)%s))\n'
                '  (property "Footprint" %s (at %s %s 0) (effects (font (size 1.27 1.27)) hide))\n'
                '  (property "Datasheet" %s (at %s %s 0) (effects (font (size 1.27 1.27)) hide))\n%s\n'
                '  (instances (project %s (path "/%s/%s" (reference %s) (unit 1)))))' % (
                    q("%s:%s" % (LIB, libref)), n(x), n(y), ang, mirror, "yes" if fp else "no", q(uid(*key)),
                    q(des), n(dx), n(dy), hide_ref, q(value), n(cx), n(cy), hide_val,
                    q("%s:%s" % (FP_LIB, fp) if fp else ""), n(x), n(y),
                    q(info[libref]["params"].get("ComponentLink1URL", "")), n(x), n(y), pin_xml,
                    q(PROJECT), ROOT_UUID, sid, q(des)))
        elif t == "15":
            kids = children.get(i, [])
            child = stem_of(next(k["TEXT"] for k in kids if k.get("RECORD") == "33"))
            x, y = X(fi(r, "LOCATION.X")), Y(fi(r, "LOCATION.Y"))
            w, h = fi(r, "XSIZE") * U, fi(r, "YSIZE") * U
            out.append(
                '(sheet (at %s %s) (size %s %s) (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (fields_autoplaced no)\n'
                '  (stroke (width 0.1524) (type solid)) (fill (color 0 0 0 0.0))\n'
                '  (uuid %s)\n'
                '  (property "Sheetname" %s (at %s %s 0) (effects (font (size 1.27 1.27)) (justify left bottom)))\n'
                '  (property "Sheetfile" %s (at %s %s 0) (effects (font (size 1.27 1.27)) (justify left top)))\n'
                '  (instances (project %s (path "/%s" (page %s)))))' % (
                    n(x), n(y), n(w), n(h), q(sheet_uuid(child)), q(child), n(x), n(y - 0.7),
                    q(child + ".kicad_sch"), n(x), n(y + h + 0.7), q(PROJECT), ROOT_UUID, q(str(SHEET_PAGES[child]))))

    tb = ('(title_block (title %s) (date "2017-06-01") (rev "V1I1") (company "Ancient Egypt Ltda")\n'
          '  (comment 1 "Pyramides of Giza, 777") (comment 2 "Cairo") (comment 3 "www.ancientegypt.com.eg"))' % q(title))
    return ('(kicad_sch (version 20250114) (generator "altium_sch_to_kicad") (generator_version "1.0")\n'
            '(uuid %s)\n(paper %s)\n%s\n(lib_symbols\n%s\n)\n%s\n(sheet_instances (path "/" (page "1")))\n)\n') % (
        q(sid), '"A3"' if big else '"A4"', tb, embed_symbols(libs_text, sorted(set(comp_names))), "\n".join(out))


def main():
    info, libs_text = convert_lib()
    for src, stem, title in FILES:
        with open(os.path.join(OUT, stem + ".kicad_sch"), "w", encoding="utf8") as f:
            f.write(convert_sheet(src, stem, title, info, libs_text))
        print("wrote", stem + ".kicad_sch")


if __name__ == "__main__":
    main()
