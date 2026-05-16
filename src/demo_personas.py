"""V11.0 — per-industry demo personas.

The 11 industries beyond septic each ship with a curated set of demo
personas that drive both the chat caller list and (when seeded) the
portal Today feed. Septic personas live in `src/demo_seed.py` for
backwards compatibility with the pre-V11.0 single-industry pattern.

Tier 1 (HVAC, Real Estate)        : 6 personas — full demo flow
Tier 2 (Plumbing, Roofing,
        Construction,
        Property Management,
        Electrical)               : 4 personas — production-grade range
Tier 3 (Landscaping, Restoration,
        Med Spa, Legal Intake)    : 3 personas — believable spread

Each persona follows the same shape used by the existing septic
scenarios — see `src/demo_seed.py`'s `_SCENARIOS` for the canonical
example. Phone numbers use the +15550102XXX–+15550203XXX block which
falls inside the NANP fictional 555 range and never collides with
real numbers.

Phone-range mapping (industry → exchange · line):
    hvac                 +1-555-010-2XXX
    real_estate          +1-555-010-3XXX
    plumbing             +1-555-010-4XXX
    roofing              +1-555-010-5XXX
    construction         +1-555-010-6XXX
    property_management  +1-555-010-7XXX
    electrical           +1-555-010-8XXX
    landscaping          +1-555-010-9XXX
    restoration          +1-555-020-1XXX
    med_spa              +1-555-020-2XXX
    legal_intake         +1-555-020-3XXX

Caller IDs are slug-prefixed (e.g., `hvac_marcus`, `re_caleb`) so the
memory.json lookup table can hold personas from multiple industries
without collisions. Each persona's `industry` field is set so
list_personas(industry=...) can filter.
"""
from __future__ import annotations


# ─────────────────────────────────────────────────────────────────────
# HVAC (Tier 1) — 6 personas
# ─────────────────────────────────────────────────────────────────────

_HVAC = [
    {
        # Emergency AC failure with a baby in the house — 6 hours ago.
        "industry":      "hvac",
        "phone":         "+15550102001",
        "caller_id":     "hvac_marcus",
        "first_name":    "Marcus",
        "last_name":     "Reilly",
        "address":       "4729 Maple Lane, Lancaster",
        "scenario_hint": "AC failure at 9pm — house is hot and there's a baby. Try saying your AC just died.",
        "voice": {
            "minutes_ago":  6 * 60,
            "duration_s":   52,
            "outcome":      "emergency_transfer",
            "emergency":    True,
            "summary":      "AC failure — emergency transfer to Mike.",
            "turns": [
                ("user",      "Hey, our AC just totally stopped and the "
                              "house is at 87 — we have a baby."),
                ("assistant", "That's not good. I'm flagging this as urgent "
                              "— getting Mike on it right now. Still at "
                              "4729 Maple?"),
                ("user",      "Yes."),
                ("assistant", "He'll call within 20 minutes. Keep the kids "
                              "in the coolest room — basement if you have "
                              "one. Hang tight."),
            ],
        },
        "sms": None,
    },
    {
        # Pre-season furnace tune-up booking — yesterday afternoon.
        "industry":      "hvac",
        "phone":         "+15550102002",
        "caller_id":     "hvac_wendy",
        "first_name":    "Wendy",
        "last_name":     "Larsen",
        "address":       "218 Linden Drive, Lancaster",
        "scenario_hint": "Returning customer — pre-winter furnace tune-up.",
        "voice": {
            "minutes_ago":  26 * 60,
            "duration_s":   88,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Tune-up scheduled — Thursday 1pm.",
            "turns": [
                ("user",      "Hi, I want to schedule a furnace tune-up "
                              "before it gets cold."),
                ("assistant", "Good thinking — getting ahead of the season. "
                              "Have you been with us before?"),
                ("user",      "Yeah, you came out last fall — 218 Linden."),
                ("assistant", "Got you. Thursday afternoon — 1pm or 3pm?"),
                ("user",      "1 is great."),
                ("assistant", "Booked. Mike will text Wednesday to confirm."),
            ],
        },
        "sms": None,
    },
    {
        # Heat pump replacement quote — 2 days ago.
        "industry":      "hvac",
        "phone":         "+15550102003",
        "caller_id":     "hvac_henry",
        "first_name":    "Henry",
        "last_name":     "Patel",
        "address":       "",
        "scenario_hint": "Quote inquiry — heat pump replacement.",
        "voice": {
            "minutes_ago":  2 * 24 * 60 + 90,
            "duration_s":   42,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Heat pump quote — in-home estimate offered.",
            "turns": [
                ("user",      "What does a new heat pump cost?"),
                ("assistant", "Depends on the size of the home and the "
                              "system, but most run $7k to $12k installed. "
                              "Want to schedule a free in-home estimate? "
                              "Mike will measure and quote exactly."),
                ("user",      "Sure — how does that work?"),
                ("assistant", "He swings by, takes about 30 minutes, no "
                              "obligation. What's a good day this week?"),
                ("user",      "Friday morning?"),
                ("assistant", "Done. Address?"),
            ],
        },
        "sms": None,
    },
    {
        # Smart-thermostat connectivity issue — 4 hours ago.
        "industry":      "hvac",
        "phone":         "+15550102004",
        "caller_id":     "hvac_carla",
        "first_name":    "Carla",
        "last_name":     "Mendez",
        "address":       "88 Hawthorne Avenue, Lancaster",
        "scenario_hint": "Thermostat issue — house isn't cooling. Try saying the thermostat won't connect.",
        "voice": {
            "minutes_ago":  4 * 60 + 12,
            "duration_s":   71,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Thermostat triage — service call scheduled for tomorrow.",
            "turns": [
                ("user",      "My thermostat keeps losing connection and "
                              "the house won't cool."),
                ("assistant", "That's frustrating. Have you tried pulling "
                              "the thermostat off and resetting it?"),
                ("user",      "A couple times. It reconnects then drops."),
                ("assistant", "Sounds like a wiring or control board issue "
                              "— worth a quick service call. Tomorrow "
                              "afternoon work?"),
                ("user",      "Yes please."),
                ("assistant", "Booked. Address?"),
                ("user",      "88 Hawthorne."),
                ("assistant", "Got it — see you tomorrow."),
            ],
        },
        "sms": None,
    },
    {
        # After-hours SMS quote request for ductwork — 12 hours ago.
        "industry":      "hvac",
        "phone":         "+15550102005",
        "caller_id":     "hvac_robert",
        "first_name":    "Robert",
        "last_name":     "Kim",
        "address":       "1240 Riverside Drive, Lancaster",
        "scenario_hint": "After-hours SMS — ductwork repair quote request.",
        "voice": None,
        "sms": {
            "minutes_ago":  12 * 60,
            "turns": [
                ("user",      "Hey, can someone come look at my ductwork? "
                              "Bedroom upstairs isn't getting any air."),
                ("assistant", "Sure thing. Mike does a free walk-through "
                              "for ductwork. What day works this week?"),
                ("user",      "Thursday or Friday."),
                ("assistant", "Got it. He'll call you tomorrow to set a "
                              "time. Address?"),
                ("user",      "1240 Riverside."),
                ("assistant", "Talk soon."),
            ],
        },
    },
    {
        # Fresh new caller — no history. Demonstrates first-contact behavior.
        "industry":      "hvac",
        "phone":         "+15550102006",
        "caller_id":     "hvac_grace",
        "first_name":    "Grace",
        "last_name":     "Ortiz",
        "address":       "",
        "scenario_hint": "Fresh caller — no history. Try anything.",
        "voice": None,
        "sms": None,
    },
]


# ─────────────────────────────────────────────────────────────────────
# REAL ESTATE (Tier 1) — 6 personas
# ─────────────────────────────────────────────────────────────────────

_REAL_ESTATE = [
    {
        # Hot buyer lead on a Saturday — 1 hour ago. The canonical
        # speed-to-lead scenario the real-estate brief calls out.
        "industry":      "real_estate",
        "phone":         "+15550103001",
        "caller_id":     "re_caleb",
        "first_name":    "Caleb",
        "last_name":     "Morrison",
        "address":       "",
        "scenario_hint": "Buyer inquiry on Birch Road. Try asking if 1100 Birch is still available.",
        "voice": {
            "minutes_ago":  62,
            "duration_s":   38,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Showing scheduled — Saturday 1pm at 1100 Birch.",
            "turns": [
                ("user",      "Hey, is 1100 Birch still on the market?"),
                ("assistant", "It is. Want to swing by this weekend or "
                              "get the details first?"),
                ("user",      "A tour would be great — Saturday afternoon?"),
                ("assistant", "Got 1pm or 3pm open."),
                ("user",      "1pm."),
                ("assistant", "Saturday 1pm. What's the best number for a "
                              "confirmation text Saturday morning?"),
                ("user",      "This one's fine."),
                ("assistant", "Lauren will be there. Talk soon."),
            ],
        },
        "sms": None,
    },
    {
        # Post open-house disclosure request — 4 hours ago.
        "industry":      "real_estate",
        "phone":         "+15550103002",
        "caller_id":     "re_priya",
        "first_name":    "Priya",
        "last_name":     "Shah",
        "address":       "",
        "scenario_hint": "Visited the Sunday open house — asking for the disclosure packet.",
        "voice": {
            "minutes_ago":  4 * 60 + 15,
            "duration_s":   46,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Disclosure emailed — second tour offered.",
            "turns": [
                ("user",      "Hi — I came to the open house Sunday on "
                              "Birch. Could I get the disclosure packet?"),
                ("assistant", "Of course. What's the best email?"),
                ("user",      "priya.shah@gmail.com."),
                ("assistant", "Lauren will send it within the hour. "
                              "Are you thinking a second look, or just "
                              "reviewing the paperwork first?"),
                ("user",      "Probably a second look — I want to bring "
                              "my partner."),
                ("assistant", "Great. Lauren has Saturday open if you "
                              "want to text her a time."),
            ],
        },
        "sms": None,
    },
    {
        # Seller wanting to list — 1 day ago. Slower-cycle but valuable.
        "industry":      "real_estate",
        "phone":         "+15550103003",
        "caller_id":     "re_daniel",
        "first_name":    "Daniel",
        "last_name":     "Ellis",
        "address":       "812 Westwood Court, Lancaster",
        "scenario_hint": "Seller inquiry — wants a CMA. Try asking what your commission is.",
        "voice": {
            "minutes_ago":  28 * 60,
            "duration_s":   95,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Seller lead — CMA requested; Lauren to call back.",
            "turns": [
                ("user",      "I'm thinking about listing my house — what's "
                              "your commission?"),
                ("assistant", "That's a conversation Lauren wants to have "
                              "with you directly — every situation's a "
                              "little different. Are you looking to list "
                              "soon or just gathering info?"),
                ("user",      "Probably within a couple months."),
                ("assistant", "Good timing. Lauren can put together a CMA "
                              "for your place — comp analysis, list price "
                              "recommendation. What's the address?"),
                ("user",      "812 Westwood Court."),
                ("assistant", "Got it. She'll call you tomorrow morning."),
            ],
        },
        "sms": None,
    },
    {
        # Returning buyer on Birch — asking about negotiation — 6 hours ago.
        "industry":      "real_estate",
        "phone":         "+15550103004",
        "caller_id":     "re_sienna",
        "first_name":    "Sienna",
        "last_name":     "Park",
        "address":       "",
        "scenario_hint": "Returning buyer — asking if sellers are flexible on price.",
        "voice": {
            "minutes_ago":  6 * 60 + 5,
            "duration_s":   54,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Buyer follow-up — Lauren to call back re: offer strategy.",
            "turns": [
                ("user",      "Hi — I toured 1100 Birch last week. Are "
                              "the sellers being flexible on price?"),
                ("assistant", "That's something Lauren will know best — "
                              "she's been talking to the listing agent. "
                              "Want me to have her call you?"),
                ("user",      "Yes please."),
                ("assistant", "What's the best time today?"),
                ("user",      "After 5."),
                ("assistant", "She'll ring you after 5. Anything specific "
                              "she should be ready to discuss?"),
                ("user",      "Just whether an offer in the high 360s would "
                              "land."),
                ("assistant", "Noted — she'll come prepared."),
            ],
        },
        "sms": None,
    },
    {
        # Active showing — lockbox emergency — 15 minutes ago. The
        # canonical real-estate "emergency" — agent paged immediately.
        "industry":      "real_estate",
        "phone":         "+15550103005",
        "caller_id":     "re_jordan",
        "first_name":    "Jordan",
        "last_name":     "Bailey",
        "address":       "1100 Birch Road, Lancaster",
        "scenario_hint": "At the showing right now — lockbox won't open. Try saying the lockbox is stuck.",
        "voice": {
            "minutes_ago":  15,
            "duration_s":   22,
            "outcome":      "emergency_transfer",
            "emergency":    True,
            "summary":      "Lockbox jammed at active showing — Lauren paged.",
            "turns": [
                ("user",      "Hi, I'm at the Birch showing and the "
                              "lockbox isn't opening."),
                ("assistant", "That's frustrating — I'm paging Lauren "
                              "right now. Are you at the front door?"),
                ("user",      "Yes, and the buyer's with me."),
                ("assistant", "Hold tight — she'll be in touch within "
                              "90 seconds."),
            ],
        },
        "sms": None,
    },
    {
        # After-hours Zillow lead — 15 hours ago. SMS thread shows the
        # cross-channel continuity the real-estate brief emphasizes.
        "industry":      "real_estate",
        "phone":         "+15550103006",
        "caller_id":     "re_emily",
        "first_name":    "Emily",
        "last_name":     "Rodriguez",
        "address":       "",
        "scenario_hint": "Saw a listing on Zillow last night — wants details.",
        "voice": {
            "minutes_ago":  15 * 60,
            "duration_s":   34,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "After-hours Zillow lead — Saturday showing booked.",
            "turns": [
                ("user",      "Hi, I saw your Birch Road listing on Zillow "
                              "— is it still available?"),
                ("assistant", "It is. Want to schedule a tour?"),
                ("user",      "Saturday afternoon would work."),
                ("assistant", "1pm or 3pm — which?"),
                ("user",      "3pm."),
                ("assistant", "Booked. Best number for a Saturday morning "
                              "confirm?"),
                ("user",      "This one."),
                ("assistant", "Talk Saturday."),
            ],
        },
        "sms": {
            "minutes_after_voice": 1,
            "turns": [
                ("assistant", "Hi Emily — Saturday 3pm at 1100 Birch is "
                              "locked in. Lauren will meet you there. "
                              "Any questions before then, text back."),
            ],
        },
    },
]


# ─────────────────────────────────────────────────────────────────────
# PLUMBING (Tier 2) — 4 personas
# ─────────────────────────────────────────────────────────────────────

_PLUMBING = [
    {
        # Burst pipe emergency — 2 hours ago.
        "industry":      "plumbing",
        "phone":         "+15550104001",
        "caller_id":     "plumb_anita",
        "first_name":    "Anita",
        "last_name":     "Brooks",
        "address":       "319 Cedar Lane, Lancaster",
        "scenario_hint": "Burst pipe in the wall — water everywhere. Try saying a pipe burst.",
        "voice": {
            "minutes_ago":  2 * 60 + 8,
            "duration_s":   41,
            "outcome":      "emergency_transfer",
            "emergency":    True,
            "summary":      "Pipe burst behind wall — tech dispatched to 319 Cedar.",
            "turns": [
                ("user",      "I have a pipe that just burst in the wall "
                              "— water is everywhere."),
                ("assistant", "I'm dispatching a tech right now. Have you "
                              "shut off the main?"),
                ("user",      "Yes, just did."),
                ("assistant", "Good. 319 Cedar — that's your address on "
                              "file?"),
                ("user",      "Yes."),
                ("assistant", "Tech ETA 30 minutes. Move what you can "
                              "off the floor until then."),
            ],
        },
        "sms": None,
    },
    {
        # Water heater quote — yesterday.
        "industry":      "plumbing",
        "phone":         "+15550104002",
        "caller_id":     "plumb_tony",
        "first_name":    "Tony",
        "last_name":     "Russo",
        "address":       "1428 Oak Hollow Drive, Lancaster",
        "scenario_hint": "Water heater stopped — wants a replacement quote.",
        "voice": {
            "minutes_ago":  22 * 60,
            "duration_s":   66,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Water heater quote — service call set for tomorrow.",
            "turns": [
                ("user",      "My water heater stopped working. How much "
                              "for a new one?"),
                ("assistant", "Depends on the size and whether you go "
                              "gas, electric, or tankless. Usually $1,500 "
                              "to $3,500 installed. Want Dave to swing by "
                              "and quote exactly?"),
                ("user",      "Sure. Tomorrow?"),
                ("assistant", "He has 10 or 2."),
                ("user",      "10."),
                ("assistant", "Booked. 1428 Oak Hollow?"),
                ("user",      "Yep."),
                ("assistant", "See you at 10."),
            ],
        },
        "sms": None,
    },
    {
        # Drain backup — 5 hours ago.
        "industry":      "plumbing",
        "phone":         "+15550104003",
        "caller_id":     "plumb_megan",
        "first_name":    "Megan",
        "last_name":     "Cole",
        "address":       "76 River Road, Lancaster",
        "scenario_hint": "Kitchen drain fully backed up — try saying your drain is clogged.",
        "voice": {
            "minutes_ago":  5 * 60 + 22,
            "duration_s":   58,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Drain clog — tech scheduled this afternoon.",
            "turns": [
                ("user",      "My kitchen drain is completely backed up — "
                              "water just sits there."),
                ("assistant", "Have you tried a plunger or a snake?"),
                ("user",      "Plunger, didn't help."),
                ("assistant", "Sounds like the trap or the line past it "
                              "— we can have a tech out this afternoon. "
                              "76 River?"),
                ("user",      "Yes."),
                ("assistant", "He'll be there between 2 and 4."),
            ],
        },
        "sms": None,
    },
    {
        # Toilet replacement quote — SMS thread, 8 hours ago.
        "industry":      "plumbing",
        "phone":         "+15550104004",
        "caller_id":     "plumb_pete",
        "first_name":    "Pete",
        "last_name":     "Carlson",
        "address":       "455 Elmwood Avenue, Lancaster",
        "scenario_hint": "SMS — wants a quote to replace two toilets.",
        "voice": None,
        "sms": {
            "minutes_ago":  8 * 60,
            "turns": [
                ("user",      "Hey, what does it cost to swap two toilets? "
                              "Old ones are running constantly."),
                ("assistant", "Labor is around $250 each — toilet itself "
                              "is whatever you pick. Want Dave to come look "
                              "and confirm there's no flange issue?"),
                ("user",      "Yeah."),
                ("assistant", "He'll call you tomorrow morning. 455 "
                              "Elmwood?"),
                ("user",      "Right."),
                ("assistant", "Talk tomorrow."),
            ],
        },
    },
]


# ─────────────────────────────────────────────────────────────────────
# ROOFING (Tier 2) — 4 personas
# ─────────────────────────────────────────────────────────────────────

_ROOFING = [
    {
        # Active leak during a storm — 1 hour ago.
        "industry":      "roofing",
        "phone":         "+15550105001",
        "caller_id":     "roof_brad",
        "first_name":    "Brad",
        "last_name":     "Mitchell",
        "address":       "623 Hillcrest Drive, Lancaster",
        "scenario_hint": "Active leak — water coming through the ceiling. Try saying you have an active roof leak.",
        "voice": {
            "minutes_ago":  62,
            "duration_s":   47,
            "outcome":      "emergency_transfer",
            "emergency":    True,
            "summary":      "Active leak — tarp crew dispatched.",
            "turns": [
                ("user",      "I've got water coming through the ceiling "
                              "in the upstairs bedroom right now."),
                ("assistant", "Is it still actively raining outside?"),
                ("user",      "Yes."),
                ("assistant", "Dispatching a tarp crew. Address?"),
                ("user",      "623 Hillcrest."),
                ("assistant", "They're 25 minutes out. Move what you can "
                              "from underneath — they'll tarp from above."),
            ],
        },
        "sms": None,
    },
    {
        # Hail damage insurance claim — 2 days ago.
        "industry":      "roofing",
        "phone":         "+15550105002",
        "caller_id":     "roof_kim",
        "first_name":    "Kim",
        "last_name":     "Davis",
        "address":       "1170 Sycamore Street, Lancaster",
        "scenario_hint": "Insurance claim after hailstorm — inspection scheduled.",
        "voice": {
            "minutes_ago":  2 * 24 * 60 + 60,
            "duration_s":   72,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Hail-damage inspection — State Farm claim; visit scheduled Tuesday.",
            "turns": [
                ("user",      "Last week's hailstorm — my insurance wants "
                              "an inspection."),
                ("assistant", "Sure, we do those. Which carrier?"),
                ("user",      "State Farm."),
                ("assistant", "Got it. Ryan will document and submit to "
                              "the adjuster directly. Tuesday or Thursday "
                              "this week?"),
                ("user",      "Tuesday."),
                ("assistant", "1170 Sycamore?"),
                ("user",      "Yes."),
                ("assistant", "He'll text Tuesday morning to confirm time."),
            ],
        },
        "sms": None,
    },
    {
        # Tear-off and replacement quote — 1 day ago.
        "industry":      "roofing",
        "phone":         "+15550105003",
        "caller_id":     "roof_owen",
        "first_name":    "Owen",
        "last_name":     "Tucker",
        "address":       "2440 Pine Ridge Court, Lancaster",
        "scenario_hint": "Old roof is past due — wants a full replacement quote.",
        "voice": {
            "minutes_ago":  26 * 60,
            "duration_s":   85,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Tear-off quote — Ryan to walk the roof Friday.",
            "turns": [
                ("user",      "My roof is 22 years old, time to replace. "
                              "What's it run?"),
                ("assistant", "Depends on size, pitch, and material. Most "
                              "tear-offs in this area land $12k to $22k. "
                              "Want Ryan to walk it and quote exact?"),
                ("user",      "Yeah."),
                ("assistant", "He has Friday morning open."),
                ("user",      "Friday works."),
                ("assistant", "Address?"),
                ("user",      "2440 Pine Ridge Court."),
                ("assistant", "Booked."),
            ],
        },
        "sms": None,
    },
    {
        # Pre-sale inspection — 6 hours ago.
        "industry":      "roofing",
        "phone":         "+15550105004",
        "caller_id":     "roof_lisa",
        "first_name":    "Lisa",
        "last_name":     "Yoon",
        "address":       "388 Magnolia Street, Lancaster",
        "scenario_hint": "Selling the house — needs a roof inspection before listing.",
        "voice": {
            "minutes_ago":  6 * 60 + 18,
            "duration_s":   54,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Pre-sale inspection — scheduled for Wednesday.",
            "turns": [
                ("user",      "I'm listing my house and want to get the "
                              "roof inspected first."),
                ("assistant", "Good idea — catches anything before the "
                              "buyer's inspector does. Standard inspection "
                              "is $150, fully credited if you end up "
                              "needing work."),
                ("user",      "Sure, let's book it."),
                ("assistant", "Wednesday morning?"),
                ("user",      "Yes."),
                ("assistant", "Address?"),
                ("user",      "388 Magnolia."),
                ("assistant", "Ryan will be there 9am."),
            ],
        },
        "sms": None,
    },
]


# ─────────────────────────────────────────────────────────────────────
# CONSTRUCTION (Tier 2) — 4 personas
# ─────────────────────────────────────────────────────────────────────

_CONSTRUCTION = [
    {
        # Kitchen remodel estimate — 5 hours ago.
        "industry":      "construction",
        "phone":         "+15550106001",
        "caller_id":     "constr_paul",
        "first_name":    "Paul",
        "last_name":     "Anderson",
        "address":       "927 Crestview Lane, Lancaster",
        "scenario_hint": "Kitchen remodel estimate request. Try asking how much for a remodel.",
        "voice": {
            "minutes_ago":  5 * 60 + 5,
            "duration_s":   92,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Kitchen remodel — site visit Friday for full estimate.",
            "turns": [
                ("user",      "Looking to remodel our kitchen — what's "
                              "the cost?"),
                ("assistant", "Too many variables to quote over the phone "
                              "— cabinets and counters alone swing huge. "
                              "Most full kitchens we do land $40k to $90k. "
                              "Carlos will come out, look, and put together "
                              "a real number. Sound good?"),
                ("user",      "Yes."),
                ("assistant", "He has Friday morning open."),
                ("user",      "Friday works."),
                ("assistant", "Address?"),
                ("user",      "927 Crestview."),
                ("assistant", "Booked."),
            ],
        },
        "sms": None,
    },
    {
        # Addition project — 1 day ago.
        "industry":      "construction",
        "phone":         "+15550106002",
        "caller_id":     "constr_julia",
        "first_name":    "Julia",
        "last_name":     "Sanchez",
        "address":       "1503 Beverly Avenue, Lancaster",
        "scenario_hint": "Wants a contractor for a master-suite addition.",
        "voice": {
            "minutes_ago":  24 * 60 + 22,
            "duration_s":   78,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Addition estimate — Carlos to walk site Thursday.",
            "turns": [
                ("user",      "We want to add a master suite on the back "
                              "of the house. Where do we start?"),
                ("assistant", "Big project — first step is a site visit "
                              "so Carlos can see what we're working with. "
                              "Are we talking footprint or going up?"),
                ("user",      "Out — single story extension."),
                ("assistant", "Got it. Thursday afternoon?"),
                ("user",      "Yes."),
                ("assistant", "Address?"),
                ("user",      "1503 Beverly."),
                ("assistant", "He'll be there at 2."),
            ],
        },
        "sms": None,
    },
    {
        # Basement finish — 2 days ago.
        "industry":      "construction",
        "phone":         "+15550106003",
        "caller_id":     "constr_dave",
        "first_name":    "Dave",
        "last_name":     "Kowalski",
        "address":       "44 Stonebridge Drive, Lancaster",
        "scenario_hint": "Wants basement finished — timeline question.",
        "voice": {
            "minutes_ago":  2 * 24 * 60 + 180,
            "duration_s":   64,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Basement finish — estimate visit scheduled next week.",
            "turns": [
                ("user",      "How long does a basement finish usually "
                              "take?"),
                ("assistant", "Typically 6 to 10 weeks once we start — "
                              "framing, electrical, drywall, finish. "
                              "Permit timeline depends on the township. "
                              "Carlos can give you a real number after a "
                              "site visit."),
                ("user",      "Let's set that up."),
                ("assistant", "Next Wednesday?"),
                ("user",      "Yes."),
                ("assistant", "Address?"),
                ("user",      "44 Stonebridge."),
                ("assistant", "He'll be there 10am."),
            ],
        },
        "sms": None,
    },
    {
        # SMS — deck and permit question, 12 hours ago.
        "industry":      "construction",
        "phone":         "+15550106004",
        "caller_id":     "constr_amy",
        "first_name":    "Amy",
        "last_name":     "Foster",
        "address":       "726 Briar Lane, Lancaster",
        "scenario_hint": "SMS — deck addition, asking about permits.",
        "voice": None,
        "sms": {
            "minutes_ago":  12 * 60,
            "turns": [
                ("user",      "Question — adding a deck. Do you handle "
                              "the permit or is that on me?"),
                ("assistant", "We pull the permit. Comes out of the project "
                              "cost, no extra fee from us. Want Carlos to "
                              "swing by and quote?"),
                ("user",      "Yes — 726 Briar."),
                ("assistant", "He'll call tomorrow morning to set a time."),
            ],
        },
    },
]


# ─────────────────────────────────────────────────────────────────────
# PROPERTY MANAGEMENT (Tier 2) — 4 personas
# ─────────────────────────────────────────────────────────────────────

_PROPERTY_MANAGEMENT = [
    {
        # Tenant emergency — no heat, 3 hours ago.
        "industry":      "property_management",
        "phone":         "+15550107001",
        "caller_id":     "pm_jasmine",
        "first_name":    "Jasmine",
        "last_name":     "Lee",
        "address":       "Unit 4B · 218 Fifth Street",
        "scenario_hint": "Tenant — no heat, baby in the apartment. Try saying your heat is out.",
        "voice": {
            "minutes_ago":  3 * 60 + 14,
            "duration_s":   42,
            "outcome":      "emergency_transfer",
            "emergency":    True,
            "summary":      "Tenant no-heat emergency — on-call maintenance dispatched.",
            "turns": [
                ("user",      "Hi, my heat is out and I have a baby — "
                              "Unit 4B at 218 Fifth Street."),
                ("assistant", "I'm dispatching the on-call maintenance "
                              "tech right now. You're in 4B?"),
                ("user",      "Yes."),
                ("assistant", "He'll be there within the hour. Layer the "
                              "baby up and stay in the smallest room you "
                              "can close off."),
            ],
        },
        "sms": None,
    },
    {
        # Prospective tenant — tour request, 1 day ago.
        "industry":      "property_management",
        "phone":         "+15550107002",
        "caller_id":     "pm_alex",
        "first_name":    "Alex",
        "last_name":     "Pham",
        "address":       "",
        "scenario_hint": "Prospect — wants to tour the unit on 5th Street.",
        "voice": {
            "minutes_ago":  24 * 60 + 30,
            "duration_s":   58,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Tour scheduled — Saturday 11am at 218 Fifth.",
            "turns": [
                ("user",      "Hi — is the unit on Fifth Street still "
                              "available?"),
                ("assistant", "Which one — the 1-bedroom 3B or the "
                              "2-bedroom 4B?"),
                ("user",      "The 2-bedroom."),
                ("assistant", "Still available. Want to see it?"),
                ("user",      "Saturday morning?"),
                ("assistant", "11 works."),
                ("user",      "Great."),
                ("assistant", "Best number for a Saturday text?"),
                ("user",      "This one."),
                ("assistant", "Karen will meet you there at 11."),
            ],
        },
        "sms": None,
    },
    {
        # Tenant lockout — 8 hours ago.
        "industry":      "property_management",
        "phone":         "+15550107003",
        "caller_id":     "pm_brian",
        "first_name":    "Brian",
        "last_name":     "O'Reilly",
        "address":       "Unit 2A · 91 Walnut Avenue",
        "scenario_hint": "Tenant — locked out of the unit.",
        "voice": {
            "minutes_ago":  8 * 60 + 10,
            "duration_s":   36,
            "outcome":      "emergency_transfer",
            "emergency":    True,
            "summary":      "Lockout — on-call maintenance with master key dispatched.",
            "turns": [
                ("user",      "I locked myself out of 2A on Walnut."),
                ("assistant", "Dispatching maintenance with the master "
                              "key — he's 15 minutes out."),
                ("user",      "Thank you."),
                ("assistant", "Take a seat in the hallway. He'll find you."),
            ],
        },
        "sms": None,
    },
    {
        # Application status check — SMS, 6 hours ago.
        "industry":      "property_management",
        "phone":         "+15550107004",
        "caller_id":     "pm_renee",
        "first_name":    "Renée",
        "last_name":     "Walker",
        "address":       "",
        "scenario_hint": "SMS — applicant checking on her application status.",
        "voice": None,
        "sms": {
            "minutes_ago":  6 * 60,
            "turns": [
                ("user",      "Hi — checking on my application for the "
                              "Cedar Park townhouse. Submitted Monday."),
                ("assistant", "Let me get Karen looking — she should "
                              "have an update by end of day. Best number?"),
                ("user",      "This one."),
                ("assistant", "She'll text you before 5."),
            ],
        },
    },
]


# ─────────────────────────────────────────────────────────────────────
# ELECTRICAL (Tier 2) — 4 personas
# ─────────────────────────────────────────────────────────────────────

_ELECTRICAL = [
    {
        # Half-house outage — 4 hours ago.
        "industry":      "electrical",
        "phone":         "+15550108001",
        "caller_id":     "elec_mark",
        "first_name":    "Mark",
        "last_name":     "Henderson",
        "address":       "1108 Birchwood Court, Lancaster",
        "scenario_hint": "Lost power in half the house — try asking about a partial outage.",
        "voice": {
            "minutes_ago":  4 * 60 + 8,
            "duration_s":   58,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Partial outage diagnosis — tech scheduled for tomorrow.",
            "turns": [
                ("user",      "I just lost power in half my house — "
                              "kitchen and one bedroom are out, rest is "
                              "fine."),
                ("assistant", "Sounds like a tripped breaker or a loose "
                              "neutral. Have you checked the panel?"),
                ("user",      "Yeah, nothing tripped."),
                ("assistant", "Worth a visit. Steve has tomorrow afternoon "
                              "open — 1 or 3?"),
                ("user",      "1 please."),
                ("assistant", "Address?"),
                ("user",      "1108 Birchwood Court."),
                ("assistant", "See you tomorrow."),
            ],
        },
        "sms": None,
    },
    {
        # Sparking outlet — 30 minutes ago. Emergency.
        "industry":      "electrical",
        "phone":         "+15550108002",
        "caller_id":     "elec_nina",
        "first_name":    "Nina",
        "last_name":     "Castro",
        "address":       "682 Willowbrook Drive, Lancaster",
        "scenario_hint": "Sparking outlet — try saying an outlet is sparking.",
        "voice": {
            "minutes_ago":  32,
            "duration_s":   28,
            "outcome":      "emergency_transfer",
            "emergency":    True,
            "summary":      "Sparking outlet emergency — tech dispatched to 682 Willowbrook.",
            "turns": [
                ("user",      "An outlet in my kitchen is sparking and "
                              "I smell something burning."),
                ("assistant", "Cut the breaker to that outlet right now. "
                              "Steve is being paged. Address?"),
                ("user",      "682 Willowbrook."),
                ("assistant", "He's 20 minutes out. Leave the breaker off."),
            ],
        },
        "sms": None,
    },
    {
        # Panel upgrade quote — 1 day ago.
        "industry":      "electrical",
        "phone":         "+15550108003",
        "caller_id":     "elec_steve",
        "first_name":    "Steve",
        "last_name":     "Whitman",
        "address":       "2218 Walnut Hollow, Lancaster",
        "scenario_hint": "Wants a panel upgrade quote — 100A to 200A.",
        "voice": {
            "minutes_ago":  26 * 60,
            "duration_s":   68,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Panel upgrade — estimate visit Friday.",
            "turns": [
                ("user",      "What does a 100 to 200 amp upgrade run?"),
                ("assistant", "Usually $1,800 to $2,800 depending on the "
                              "panel and what utility coordination needs "
                              "to happen. Steve can quote exact after a "
                              "site visit. Friday work?"),
                ("user",      "Friday morning?"),
                ("assistant", "10 works."),
                ("user",      "Done."),
                ("assistant", "Address?"),
                ("user",      "2218 Walnut Hollow."),
                ("assistant", "He'll be there."),
            ],
        },
        "sms": None,
    },
    {
        # EV charger install — 2 days ago.
        "industry":      "electrical",
        "phone":         "+15550108004",
        "caller_id":     "elec_kate",
        "first_name":    "Kate",
        "last_name":     "Marshall",
        "address":       "59 Stonefield Way, Lancaster",
        "scenario_hint": "Wants an EV charger installed — Tesla wall connector.",
        "voice": {
            "minutes_ago":  2 * 24 * 60 + 240,
            "duration_s":   55,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "EV charger quote — install scheduled Thursday.",
            "turns": [
                ("user",      "I want to install a Tesla wall connector "
                              "in the garage."),
                ("assistant", "Easy — depends on panel capacity and the "
                              "run distance. Most installs land $750 to "
                              "$1,400. Steve will check your panel and "
                              "quote the wire run. Thursday work?"),
                ("user",      "Yes."),
                ("assistant", "Address?"),
                ("user",      "59 Stonefield."),
                ("assistant", "Booked Thursday morning."),
            ],
        },
        "sms": None,
    },
]


# ─────────────────────────────────────────────────────────────────────
# LANDSCAPING (Tier 3) — 3 personas
# ─────────────────────────────────────────────────────────────────────

_LANDSCAPING = [
    {
        # Weekly lawn service quote — 5 hours ago.
        "industry":      "landscaping",
        "phone":         "+15550109001",
        "caller_id":     "land_helen",
        "first_name":    "Helen",
        "last_name":     "Vargas",
        "address":       "412 Garden View Lane, Lancaster",
        "scenario_hint": "Wants weekly lawn service — half-acre property.",
        "voice": {
            "minutes_ago":  5 * 60 + 18,
            "duration_s":   62,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Weekly lawn service quote — walk-through Saturday.",
            "turns": [
                ("user",      "Looking for weekly lawn service — half-acre, "
                              "fenced yard."),
                ("assistant", "We do weekly. Half-acre usually runs $55 to "
                              "$75 a visit. Want Jen to walk it Saturday "
                              "and confirm?"),
                ("user",      "Sure."),
                ("assistant", "10am?"),
                ("user",      "Works."),
                ("assistant", "Address?"),
                ("user",      "412 Garden View."),
                ("assistant", "See you Saturday."),
            ],
        },
        "sms": None,
    },
    {
        # Tree down after storm — 2 hours ago.
        "industry":      "landscaping",
        "phone":         "+15550109002",
        "caller_id":     "land_chris",
        "first_name":    "Chris",
        "last_name":     "Boyd",
        "address":       "1840 Forest Edge Drive, Lancaster",
        "scenario_hint": "Storm took down a tree blocking the driveway.",
        "voice": {
            "minutes_ago":  2 * 60 + 4,
            "duration_s":   48,
            "outcome":      "emergency_transfer",
            "emergency":    True,
            "summary":      "Tree-down call — crew dispatched to clear driveway.",
            "turns": [
                ("user",      "A tree came down across my driveway from "
                              "last night's storm — can't get out."),
                ("assistant", "Jen's crew can come clear it. Anyone "
                              "hurt? Anything on the house?"),
                ("user",      "No, just the driveway."),
                ("assistant", "Crew will be there within an hour. "
                              "Address?"),
                ("user",      "1840 Forest Edge."),
                ("assistant", "They'll text on the way."),
            ],
        },
        "sms": None,
    },
    {
        # Patio design quote — SMS, 1 day ago.
        "industry":      "landscaping",
        "phone":         "+15550109003",
        "caller_id":     "land_aisha",
        "first_name":    "Aisha",
        "last_name":     "Diallo",
        "address":       "78 Sunset Boulevard, Lancaster",
        "scenario_hint": "SMS — wants a quote for a new patio.",
        "voice": None,
        "sms": {
            "minutes_ago":  24 * 60 + 90,
            "turns": [
                ("user",      "Hi — looking to put in a patio. Maybe "
                              "300 square feet."),
                ("assistant", "Jen does patios. Materials matter a lot "
                              "for the price — pavers vs. stamped concrete "
                              "vs. flagstone all read different. Want her "
                              "to come look and quote a couple options?"),
                ("user",      "Yes."),
                ("assistant", "She'll call tomorrow to set a time. "
                              "Address?"),
                ("user",      "78 Sunset."),
                ("assistant", "Talk tomorrow."),
            ],
        },
    },
]


# ─────────────────────────────────────────────────────────────────────
# RESTORATION (Tier 3) — 3 personas
# ─────────────────────────────────────────────────────────────────────

_RESTORATION = [
    {
        # Basement flooding right now — 30 minutes ago.
        "industry":      "restoration",
        "phone":         "+15550201001",
        "caller_id":     "rest_jacob",
        "first_name":    "Jacob",
        "last_name":     "Cole",
        "address":       "344 Brookside Drive, Lancaster",
        "scenario_hint": "Basement is flooding right now — try saying water is everywhere.",
        "voice": {
            "minutes_ago":  32,
            "duration_s":   45,
            "outcome":      "emergency_transfer",
            "emergency":    True,
            "summary":      "Active flooding — mitigation crew within the hour.",
            "turns": [
                ("user",      "My basement is filling with water from "
                              "the water heater — it's still going."),
                ("assistant", "Have you shut off the water main?"),
                ("user",      "Just did."),
                ("assistant", "Good. Mitigation crew dispatched — they'll "
                              "be there within the hour with pumps and "
                              "fans. Address?"),
                ("user",      "344 Brookside."),
                ("assistant", "Stay out of the basement — Tom will guide "
                              "you when he arrives."),
            ],
        },
        "sms": None,
    },
    {
        # Pipe burst aftermath — 6 hours ago.
        "industry":      "restoration",
        "phone":         "+15550201002",
        "caller_id":     "rest_naomi",
        "first_name":    "Naomi",
        "last_name":     "Reyes",
        "address":       "1207 Maple Ridge, Lancaster",
        "scenario_hint": "Pipe burst this morning — needs water-damage mitigation.",
        "voice": {
            "minutes_ago":  6 * 60 + 22,
            "duration_s":   72,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Water-damage mitigation — Allstate claim opened.",
            "turns": [
                ("user",      "A pipe burst this morning, plumber fixed "
                              "it but the floor and walls are soaked."),
                ("assistant", "We can mitigate today. Are you working "
                              "with insurance?"),
                ("user",      "Yeah, Allstate."),
                ("assistant", "Got it. Tom will document and submit "
                              "directly to your adjuster. He'll be there "
                              "this afternoon. Address?"),
                ("user",      "1207 Maple Ridge."),
                ("assistant", "He'll text on the way."),
            ],
        },
        "sms": None,
    },
    {
        # Smoke damage from a small fire — 1 day ago.
        "industry":      "restoration",
        "phone":         "+15550201003",
        "caller_id":     "rest_louis",
        "first_name":    "Louis",
        "last_name":     "Hartmann",
        "address":       "806 Vista Terrace, Lancaster",
        "scenario_hint": "Small kitchen fire — smoke damage throughout the house.",
        "voice": {
            "minutes_ago":  26 * 60,
            "duration_s":   88,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Smoke-damage mitigation — site visit tomorrow morning.",
            "turns": [
                ("user",      "We had a small kitchen fire — fire dept "
                              "put it out, but the whole first floor "
                              "smells like smoke."),
                ("assistant", "Smoke damage spreads further than the "
                              "fire. We do smoke remediation — odor, "
                              "soft surfaces, HVAC duct cleaning. "
                              "Working with insurance?"),
                ("user",      "Yeah, filing today."),
                ("assistant", "Tom will come tomorrow morning to scope "
                              "and document. Address?"),
                ("user",      "806 Vista Terrace."),
                ("assistant", "He'll be there 9am."),
            ],
        },
        "sms": None,
    },
]


# ─────────────────────────────────────────────────────────────────────
# MED SPA (Tier 3) — 3 personas
# ─────────────────────────────────────────────────────────────────────

_MED_SPA = [
    {
        # Botox consultation booking — 4 hours ago.
        "industry":      "med_spa",
        "phone":         "+15550202001",
        "caller_id":     "spa_olivia",
        "first_name":    "Olivia",
        "last_name":     "Bennett",
        "address":       "",
        "scenario_hint": "New client — wants to book a Botox consultation.",
        "voice": {
            "minutes_ago":  4 * 60 + 28,
            "duration_s":   62,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Consultation booked — Botox, Saturday 2pm.",
            "turns": [
                ("user",      "Hi — I'd like to book a Botox consultation."),
                ("assistant", "Welcome — happy to set that up. First "
                              "consult with Dr. Patel is complimentary. "
                              "Have you had Botox before?"),
                ("user",      "Once, a couple years ago."),
                ("assistant", "Got it. She'll go over your goals and "
                              "what's appropriate. Saturday 2pm work?"),
                ("user",      "Yes."),
                ("assistant", "Best number for a reminder text Friday?"),
                ("user",      "This one."),
                ("assistant", "See you Saturday."),
            ],
        },
        "sms": None,
    },
    {
        # Laser hair removal pricing inquiry — 1 day ago.
        "industry":      "med_spa",
        "phone":         "+15550202002",
        "caller_id":     "spa_yara",
        "first_name":    "Yara",
        "last_name":     "Hassan",
        "address":       "",
        "scenario_hint": "Inquiry — laser hair removal pricing for legs.",
        "voice": {
            "minutes_ago":  25 * 60,
            "duration_s":   54,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Pricing inquiry — consultation offered.",
            # V13.0 — V11 persona quoted "$250 for full legs" which
            # directly contradicts the system prompt's "Don't quote
            # firm prices." A real spa front desk softly redirects:
            # pricing depends on the specific area and skin, and is
            # confirmed at consult. Tone now matches the prompt.
            "turns": [
                ("user",      "How much for laser hair removal on legs?"),
                ("assistant", "It depends on the area and skin — Dr. Patel "
                              "will walk through the right package at "
                              "your consult. Consultations are complimentary. "
                              "Would you like to come in?"),
                ("user",      "Sure — sometime next week?"),
                ("assistant", "Tuesday afternoon?"),
                ("user",      "2 works."),
                ("assistant", "Best number for a reminder the day before?"),
                ("user",      "This one."),
                ("assistant", "See you Tuesday."),
            ],
        },
        "sms": None,
    },
    {
        # Appointment reschedule — SMS, 12 hours ago.
        "industry":      "med_spa",
        "phone":         "+15550202003",
        "caller_id":     "spa_chen",
        "first_name":    "Chen",
        "last_name":     "Liu",
        "address":       "",
        "scenario_hint": "SMS — needs to reschedule Thursday's appointment.",
        "voice": None,
        "sms": {
            "minutes_ago":  12 * 60,
            "turns": [
                ("user",      "Hi — I need to reschedule Thursday's filler "
                              "appointment. Work conflict."),
                ("assistant", "Of course — no problem. Friday at the same "
                              "time work, or earlier the following week?"),
                ("user",      "Friday."),
                ("assistant", "Done. You're moved to Friday at 11. "
                              "Reminder will go out Thursday evening."),
            ],
        },
    },
]


# ─────────────────────────────────────────────────────────────────────
# LEGAL INTAKE (Tier 3) — 3 personas
# ─────────────────────────────────────────────────────────────────────

_LEGAL_INTAKE = [
    {
        # Workplace issue intake — 6 hours ago.
        "industry":      "legal_intake",
        "phone":         "+15550203001",
        "caller_id":     "law_anita",
        "first_name":    "Anita",
        "last_name":     "Powell",
        "address":       "",
        "scenario_hint": "Wants to discuss a workplace issue. The AI screens for category only — no case details.",
        "voice": {
            "minutes_ago":  6 * 60 + 8,
            "duration_s":   78,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Workplace-issue intake captured — consultation booked Friday 3pm.",
            "turns": [
                ("user",      "Hi — I need to talk to someone about a "
                              "workplace situation."),
                ("assistant", "I can get you set up with a consultation. "
                              "I won't ask for details on the phone — "
                              "that conversation happens with the "
                              "attorney directly. Can I get your name "
                              "and the best callback number?"),
                ("user",      "Anita Powell — this number is fine."),
                ("assistant", "Thank you. Is this a workplace issue "
                              "broadly — discrimination, wage, contract, "
                              "or something else?"),
                ("user",      "It's a discrimination concern."),
                # V13.0 — V11 persona quoted "$250 consultation fee"
                # which contradicts the prompt's "Consultation fees
                # and scheduling are confirmed by the attorney's
                # office, not by you." Privilege-adjacent: an intake
                # screener doesn't quote fees on the screening call.
                ("assistant", "Noted. David's office will confirm "
                              "scheduling and the consult details "
                              "directly. Friday at 3 work?"),
                ("user",      "Yes."),
                ("assistant", "Booked. David will call you Friday at 3 "
                              "sharp."),
            ],
        },
        "sms": None,
    },
    {
        # Car accident intake — 1 day ago.
        "industry":      "legal_intake",
        "phone":         "+15550203002",
        "caller_id":     "law_mike",
        "first_name":    "Mike",
        "last_name":     "Saunders",
        "address":       "",
        "scenario_hint": "Was in a car accident — looking for personal injury representation.",
        "voice": {
            "minutes_ago":  24 * 60 + 12,
            "duration_s":   84,
            "outcome":      "normal",
            "emergency":    False,
            "summary":      "Personal-injury intake — consultation booked tomorrow 10am.",
            "turns": [
                ("user",      "I was in a car accident last week and "
                              "I'm looking for a lawyer."),
                ("assistant", "I'm sorry to hear that — hope you're okay. "
                              "I'll set you up with a consultation. Just "
                              "your name and a callback number for now."),
                ("user",      "Mike Saunders. This number."),
                ("assistant", "Thank you. Personal injury consults at "
                              "this firm are free — no fee unless we "
                              "take the case. Tomorrow morning work?"),
                ("user",      "10am?"),
                ("assistant", "Booked. David will call you at 10."),
            ],
        },
        "sms": None,
    },
    {
        # Time-sensitive eviction — 2 hours ago. Triggers emergency.
        "industry":      "legal_intake",
        "phone":         "+15550203003",
        "caller_id":     "law_renata",
        "first_name":    "Renata",
        "last_name":     "Cruz",
        "address":       "",
        "scenario_hint": "Received an eviction notice — court date is in 6 days. Try saying you got an eviction notice.",
        "voice": {
            "minutes_ago":  2 * 60 + 22,
            "duration_s":   58,
            "outcome":      "emergency_transfer",
            "emergency":    True,
            "summary":      "Time-sensitive eviction — David paged for same-day callback.",
            "turns": [
                ("user",      "I got served an eviction notice — court "
                              "is in 6 days. I need help."),
                ("assistant", "I understand — that's tight. I'm flagging "
                              "this as time-sensitive so David sees it "
                              "right away. Your name and the best number?"),
                ("user",      "Renata Cruz, this number."),
                ("assistant", "Thank you. David will call you back today, "
                              "within a few hours."),
            ],
        },
        "sms": None,
    },
]


# ─────────────────────────────────────────────────────────────────────
# Registry — assembled into the dict consumed by demo_seed.py
# ─────────────────────────────────────────────────────────────────────

PERSONAS_BY_INDUSTRY: dict[str, list[dict]] = {
    "hvac":                _HVAC,
    "real_estate":         _REAL_ESTATE,
    "plumbing":            _PLUMBING,
    "roofing":             _ROOFING,
    "construction":        _CONSTRUCTION,
    "property_management": _PROPERTY_MANAGEMENT,
    "electrical":          _ELECTRICAL,
    "landscaping":         _LANDSCAPING,
    "restoration":         _RESTORATION,
    "med_spa":             _MED_SPA,
    "legal_intake":        _LEGAL_INTAKE,
}


def personas_for(industry: str) -> list[dict]:
    """All personas for a given industry slug. [] for unknown slugs."""
    return list(PERSONAS_BY_INDUSTRY.get(industry, []))


def all_personas() -> list[dict]:
    """Every persona across every non-septic industry, flat list."""
    out: list[dict] = []
    for personas in PERSONAS_BY_INDUSTRY.values():
        out.extend(personas)
    return out


def industries_with_personas() -> list[str]:
    """Slugs that have at least one demo persona."""
    return [slug for slug, personas in PERSONAS_BY_INDUSTRY.items()
            if personas]
