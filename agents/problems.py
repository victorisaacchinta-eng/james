"""PROBLEMS: the common problems an object of a given kind runs into, and how people usually fix them.

The local model suggests these after IDENTIFY (it knows many more kinds of objects). This built-in catalog
is the fallback when the model is not running or returns too little, and it fills the list up to four.
Everything here is general, well-known consumer repair practice, written as suggestions, not a diagnosis.
Each fix is ordered easiest and cheapest first, with a safety note and when to hand it to a professional."""
from __future__ import annotations

import re

N_PROBLEMS = 4


def P(title, sign, steps, safety=None, pro=None, part=None):
    return {"title": title, "sign": sign, "steps": [{"title": t, "detail": d} for t, d in steps],
            "safety": safety, "pro_when": pro, "part_needed": part}


CATALOG: dict[str, dict] = {
    "phone": {
        "match": r"\b(phone|mobile|smartphone|iphone|android|galaxy|redmi|pixel|oneplus|vivo|oppo|realme|poco|nokia|motorola)",
        "problems": [
            P("Cracked screen or back glass", "Cracks, spider lines, dead touch spots or lines on the display",
              [("Check how deep the damage is", "If the picture and touch still work, only the outer glass or the "
                "screen protector may be cracked. Peel the protector off carefully and look again."),
               ("Stop the cracks spreading", "Put a tempered glass protector or clear tape over the cracks so "
                "glass splinters don't cut your finger and dust stays out."),
               ("Back up the phone", "A cracked screen can fail suddenly. Back up photos and chats now."),
               ("Replace the screen or glass", "Get a quote for the full display assembly (glass + panel) for your "
                "exact model. Check the model under Settings > About phone before you order.")],
              "Glass splinters are sharp; do not press on a badly cracked screen.",
              "Touch or display not working, or a curved/OLED screen: those need a full display assembly and heat "
              "tools to fit.", "display assembly or tempered glass protector for this model"),
            P("Battery drains fast or is swollen", "Needs charging more than once a day, shuts off early, back or "
              "screen lifting",
              [("Check battery health", "iPhone: Settings > Battery > Battery Health. Android: Settings > Battery, "
                "or the maker's diagnostics app. Below about 80% usually means a worn battery."),
               ("Find the apps using power", "The battery screen lists which apps use the most. Update or "
                "uninstall the worst ones and turn off background activity for them."),
               ("Lower the easy drains", "Reduce screen brightness and timeout, turn on battery saver, and turn off "
                "location for apps that don't need it."),
               ("Replace the battery", "A worn battery is a normal wear part. Use a genuine or good-quality "
                "battery fitted by a service centre.")],
              "A swollen battery can catch fire. Stop charging it, do not press or puncture it, and take it to a "
              "service centre.", "Swelling, heat, or the phone lifting at the seams.", "replacement battery"),
            P("Not charging or charges slowly", "Cable must be held at an angle, charging stops and starts",
              [("Try another cable and adapter", "Most charging faults are the cable. Test with a known good cable "
                "and adapter."),
               ("Clean the charging port", "Power off, then gently lift out pocket lint with a wooden toothpick. "
                "Do not use metal or liquids."),
               ("Check for a moisture warning", "If the phone shows a liquid warning, unplug and let it dry fully "
                "before charging again."),
               ("Replace the charging port", "If a clean port with a good cable still fails, the port or its "
                "board needs replacing.")],
              "Never charge a phone that shows a moisture warning or smells burnt.",
              "Port is loose, burnt, or the phone doesn't charge with any cable.", "charging port flex / board"),
            P("Camera blurry or not working", "Blurry photos, black camera screen, clicking or shaking lens",
              [("Clean the lens", "Wipe the camera glass with a microfibre cloth; fingerprints are the most "
                "common cause of blur."),
               ("Check the lens glass", "Look for a crack or chip on the small glass over the lens. A cracked lens "
                "cover makes every photo hazy."),
               ("Restart and update", "Force close the camera app, restart the phone and install updates."),
               ("Replace the lens cover or camera module", "A cracked cover is a cheap part; a module that shows "
                "black or shakes needs a new camera module.")],
              None, "Black screen in every camera app, or the lens rattles.", "camera lens glass or camera module"),
        ]},
    "laptop": {
        "match": r"laptop|notebook|macbook|chromebook|ultrabook",
        "problems": [
            P("Slow or overheating", "Fans loud, hot underside, apps freeze",
              [("Check what is using the processor", "Open Task Manager (Windows) or Activity Monitor (Mac) and "
                "close what uses the most CPU."),
               ("Give it air", "Use it on a hard, flat surface, not a bed or lap, so the vents are clear."),
               ("Clean the vents", "Power off and blow short bursts of compressed air through the vents."),
               ("Free up storage and update", "Keep at least 10 to 15% of the disk free and install system updates.")],
              "If it gets too hot to touch or shuts itself down, stop using it until it is checked.",
              "Fan grinding, or shutdowns from heat after cleaning.", None),
            P("Battery not holding charge", "Short battery life, dies at a high percentage",
              [("Check battery health", "Mac: System Settings > Battery > Battery Health. Windows: run "
                "'powercfg /batteryreport' and compare full charge to design capacity."),
               ("Calibrate", "Charge to 100%, use it down to about 5%, then charge fully again once."),
               ("Replace the battery", "Batteries are wear parts; use the right part number for your model.")],
              "A swollen laptop battery can lift the trackpad or case. Stop using and charging it.",
              "Swelling, or the laptop only runs when plugged in.", "replacement battery"),
            P("Keys or trackpad not working", "Some keys don't type, sticky keys, cursor jumps",
              [("Rule out software", "Restart, and try an external keyboard or mouse. If those work, it is the "
                "built-in part."),
               ("Clean under the keys", "Tilt the laptop and use short bursts of compressed air between the keys."),
               ("Check for spills", "Sticky keys after a spill need cleaning inside; power off at once after any spill.")],
              None, "Liquid got inside, or many keys fail together.", "keyboard or trackpad"),
            P("Screen flicker or lines", "Flickering, lines, dim or black display",
              [("Test an external monitor", "If the external screen is fine, the fault is the laptop panel or its cable."),
               ("Update graphics drivers", "Install the latest system and graphics updates."),
               ("Move the lid slowly", "If flicker changes as the lid moves, the display cable is likely worn.")],
              None, "Lines stay after updates, or the screen is cracked.", "display panel or display cable"),
        ]},
    "audio": {
        "match": r"headphone|earphone|earbud|airpods|headset|buds|speaker",
        "problems": [
            P("One side has no sound", "Sound only in one ear",
              [("Check the balance setting", "Phone or laptop sound settings have a left/right balance; centre it."),
               ("Clean the mesh", "Gently brush wax and dust off the speaker mesh with a dry soft brush."),
               ("Reset the pair", "For wireless buds, forget the device and re-pair both buds from the case.")],
              None, "Still silent after a reset and clean.", None),
            P("Won't pair over Bluetooth", "Not showing in the list, keeps disconnecting",
              [("Forget and re-pair", "Remove the device from Bluetooth settings, then put the buds in pairing mode."),
               ("Reset the earbuds", "Most have a reset: hold the case button or touch areas; check the maker's guide."),
               ("Keep them charged and close", "Low battery and distance cause dropouts.")],
              None, None, None),
            P("Crackling or low volume", "Distorted or quiet sound",
              [("Clean the nozzle", "Wax on the mesh is the most common cause of low volume."),
               ("Try another device", "If it crackles on every device, it is the headphones."),
               ("Check the cable", "For wired ones, wiggle the cable near the plug; crackles there mean a worn wire.")],
              None, "Crackle stays on every device.", "replacement ear tips or cable"),
            P("Case or buds won't charge", "No charging light, buds dead in the case",
              [("Clean the contacts", "Wipe the metal contacts in the case and on the buds with a dry cotton swab."),
               ("Try another cable", "Test the case with a known good cable and adapter."),
               ("Seat the buds", "Make sure each bud clicks fully into place.")],
              None, "Case still dead with a good cable.", None),
        ]},
    "bottle": {
        "match": r"bottle|flask|tumbler|thermos|sipper",
        "problems": [
            P("Dent in the body", "A dent or bulge on the side or base",
              [("Check it still stands and seals", "A small dent is cosmetic. Check it doesn't wobble or leak."),
               ("Plastic: warm water", "For a plastic bottle, fill with warm (not boiling) water and close the cap; "
                "the pressure can push a dent out."),
               ("Metal insulated: leave it", "Pressing a dent out of a vacuum bottle can break the vacuum seal. If "
                "it still keeps temperature, keep using it.")],
              None, "A dent on the base that makes it wobble, or it stopped insulating.", None),
            P("Leaking cap or seal", "Drips from the lid, wet bag",
              [("Check the gasket", "Pull out the rubber or silicone ring in the cap, clean it, and refit it "
                "without twists."),
               ("Check the threads", "Look for cracks in the cap threads or the rim."),
               ("Replace the gasket or lid", "Spare gaskets and lids are sold for many brands.")],
              None, None, "replacement lid gasket or cap"),
            P("Bad smell or mould", "Smell, black spots in the lid",
              [("Take the lid apart", "Mould usually hides in the lid and gasket."),
               ("Soak and scrub", "Soak in warm water with baking soda or diluted white vinegar, scrub with a "
                "bottle brush, rinse well."),
               ("Dry fully", "Let all parts air-dry open before closing it.")],
              "Don't mix bleach with vinegar or other cleaners.", None, None),
            P("Doesn't keep drinks hot or cold", "Outside feels hot or cold, drink changes temperature fast",
              [("Test it", "Fill with hot water: if the outside gets warm, the vacuum layer is lost."),
               ("Check for a dent or crack", "A hard knock can break the vacuum seal."),
               ("Replace or claim warranty", "A lost vacuum can't be repaired at home; many brands cover it.")],
              None, None, None),
        ]},
    "glasses": {
        "match": r"glasses|sunglass|spectacle|eyewear|goggle|shades",
        "problems": [
            P("Scratched lens", "Scratches in your line of sight",
              [("Clean it properly", "Rinse, use lens cleaner or mild soap, and dry with a microfibre cloth; dust "
                "can look like scratches."),
               ("Don't polish with paste", "Toothpaste and baking soda remove coatings and make it worse."),
               ("Replace the lenses", "An optician can fit new lenses into the same frame.")],
              None, "Deep scratches in prescription lenses.", "replacement lenses"),
            P("Loose hinge or arm", "Arms wobble or fall off",
              [("Tighten the hinge screw", "Use a small eyeglass screwdriver; turn gently until snug."),
               ("Replace a lost screw", "Eyeglass repair kits include the tiny screws."),
               ("Thread lock", "A tiny drop of clear nail polish on the screw head keeps it from backing out.")],
              None, "Broken hinge or a spring hinge.", "eyeglass screw kit"),
            P("Bent frame", "Sits crooked or tight on one side",
              [("Warm and adjust", "Plastic frames can be warmed with a hair dryer on low, then bent back slowly."),
               ("Metal frames: small steps", "Bend with fingers in small steps; don't force near the solder joints."),
               ("Get a free adjustment", "Most optical shops adjust frames for free.")],
              None, "Titanium or rimless frames.", None),
            P("Lens popped out", "One lens loose or missing from the frame",
              [("Check the lens edge", "If the lens is not chipped, it can usually go back in."),
               ("Refit it", "Warm plastic frames slightly and press the lens in from the front; metal frames "
                "need the rim screw loosened first."),
               ("Tighten the rim screw", "For metal frames, tighten the small screw at the rim.")],
              None, "The lens is chipped, or it keeps falling out.", None),
        ]},
    "watch": {
        "match": r"\bwatch|smartwatch|wristwatch|timepiece",
        "problems": [
            P("Stopped or losing time", "Hands stopped, time wrong",
              [("Battery (quartz)", "Most quartz watches need a new cell every 2 to 3 years."),
               ("Wind it (mechanical)", "Automatic watches stop if not worn; wind or wear them."),
               ("Smartwatch: restart and update", "Restart, charge fully and update the software.")],
              None, "It stops again right after a new battery.", "watch battery"),
            P("Scratched crystal", "Scratches on the glass",
              [("Identify the crystal", "Acrylic crystals can be polished; mineral and sapphire can't at home."),
               ("Polish acrylic", "Use a plastic polish with a soft cloth in small circles."),
               ("Replace the crystal", "A watch shop can fit a new crystal.")],
              None, "Mineral or sapphire crystal.", "watch crystal"),
            P("Strap or pin broken", "Strap loose or came off",
              [("Check the spring bar", "The small pin holding the strap often bends or is lost."),
               ("Replace the spring bar", "Match the width in mm; a spring bar tool makes it easy."),
               ("Replace the strap", "Buy a strap of the same lug width.")],
              None, None, "spring bars or strap of the same width"),
            P("Fog or water inside", "Mist under the glass",
              [("Remove it from water", "Keep it dry and stop wearing it in water."),
               ("Get it opened and dried", "Moisture rusts the movement; a watch shop should open and dry it soon."),
               ("Replace the seals", "Ask for new gaskets after any water entry.")],
              None, "Any water inside the case.", "case gaskets"),
        ]},
    "cable": {
        "match": r"cable|charger|adapter|power bank|powerbank|plug|cord",
        "problems": [
            P("Frayed or split cable", "Wires visible near the plug",
              [("Stop using it", "Exposed wires can short and overheat."),
               ("Replace it", "A certified cable is cheaper than a damaged phone."),
               ("Add strain relief", "On the new one, a spring or cable protector at the plug stops fraying.")],
              "Don't tape over exposed wires and keep using the cable.", None, "replacement cable"),
            P("Works only at an angle", "Charging stops unless held",
              [("Test another device", "If it fails on every device, it is the cable."),
               ("Clean the device port", "Lint in the port makes a loose fit."),
               ("Replace the cable", "A worn plug can't be fixed reliably.")],
              None, None, "replacement cable"),
            P("Adapter gets very hot", "Too hot to hold, smells",
              [("Unplug it", "Excess heat or a burning smell is a fire risk."),
               ("Check the rating", "Use an adapter rated for the device's power."),
               ("Replace with a certified one", "Cheap uncertified chargers are a common cause.")],
              "Stop using any charger that smells burnt, sparks or is deformed.", None, "certified charger"),
            P("Charges slowly", "Fast charge not working",
              [("Match cable and adapter", "Fast charging needs a capable adapter and cable."),
               ("Check the port", "Clean lint from the device port."),
               ("Try another adapter", "Rule out a failing charger.")],
              None, None, None),
        ]},
    "generic": {
        "match": r".*",
        "problems": [
            P("Physical damage (crack, dent or break)", "Visible crack, chip, dent or bent part",
              [("Stop using it if it is unsafe", "Sharp edges, exposed wires or leaks come first."),
               ("Find the exact part", "Look for a model number or label on the object."),
               ("Repair or replace the part", "Many parts are sold by model number.")],
              None, "It uses mains power, gas or a battery pack.", None),
            P("Loose or missing part", "Something rattles, wobbles or has fallen off",
              [("Find where it came from", "Check screws, clips and hinges."),
               ("Tighten or refit", "Use the right size screwdriver; don't overtighten plastic."),
               ("Order the part", "Search the model number plus the part name.")],
              None, None, None),
            P("Dirty, stained or corroded", "Grime, rust or green deposits",
              [("Clean gently", "Mild soap and a soft cloth first."),
               ("Treat rust or corrosion", "Light rust: fine steel wool; battery corrosion: a little white vinegar "
                "on a swab, then dry."),
               ("Dry and protect", "Dry fully; a thin coat of oil protects bare metal tools.")],
              "Wear gloves when cleaning battery leakage.", None, None),
            P("Doesn't work or turn on", "No power, no response",
              [("Check power first", "Batteries, charger, switch and socket."),
               ("Reset it", "Many devices have a reset button or a long-press reset."),
               ("Check the manual", "Search the model number plus 'not turning on'.")],
              None, "It uses mains power and won't turn on after the basics.", None),
        ]},
}


def category(obj: str) -> str:
    o = (obj or "").lower()
    for name, c in CATALOG.items():
        if name != "generic" and re.search(c["match"], o):
            return name
    return "generic"


def catalog_problems(obj: str) -> list[dict]:
    """The four built-in problems for this kind of object (no steps, as shown in the list)."""
    return [{"title": p["title"], "sign": p["sign"], "seen": False, "source": "catalog"}
            for p in CATALOG[category(obj)]["problems"]]


def catalog_fix(obj: str, title: str) -> dict | None:
    """The built-in fix for a problem title of this object's kind, or None."""
    t = (title or "").lower()
    for p in CATALOG[category(obj)]["problems"]:
        if p["title"].lower() == t or same_problem(p["title"], title):
            return {k: p[k] for k in ("steps", "safety", "pro_when", "part_needed")}
    return None


def _key(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (t or "").lower()).strip()


_STOP = {"or", "and", "the", "a", "an", "is", "not", "of", "with", "in", "on", "to", "it", "its", "no", "from", "by", "for", "at", "after"}


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[:-len(suf)]
    return w


def _words(t: str) -> set:
    return {_stem(w) for w in _key(t).split() if w not in _STOP}


def same_problem(a: str, b: str) -> bool:
    """'Cracked screen' and 'Cracked screen or back glass' are the same problem."""
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.6


def merge(obj: str, model_list, visible_problem: str | None = None) -> list[dict]:
    """Clean the model's list, put a problem visible in the photo first, fill up to four from the catalog."""
    out = []

    def add(title, sign="", is_seen=False, source="model"):
        title, sign = str(title or "").strip().rstrip("."), str(sign or "").strip()
        k = _key(title)
        if not k or "no visible" in k or len(out) >= N_PROBLEMS or any(same_problem(title, o["title"]) for o in out):
            return
        out.append({"title": title[:60], "sign": sign[:90], "seen": bool(is_seen), "source": source})

    vis = (visible_problem or "").strip()
    damage = bool(vis) and "no visible" not in vis.lower()
    items = [p for p in (model_list or []) if isinstance(p, dict)]
    if damage:
        match = next((p for p in items if p.get("seen")), None)
        add(match["title"] if match else vis, (match or {}).get("sign", "Seen in the snapshot"), True)
    for p in (sorted(items, key=lambda p: not p.get("seen")) if damage else items):   # 'seen' only counts with damage
        add(p.get("title"), p.get("sign"), bool(p.get("seen")) and damage)
    for p in catalog_problems(obj):
        add(p["title"], p["sign"], False, "catalog")
    return out
