"""V11.0 — multi-industry registry.

Single source of truth for vertical-specific copy, vocabulary, LLM
context, and operational terminology across all 12 supported industries.

Before V11.0 the system had three industries hardcoded as `<option>` tags
in `design.py`, with a one-line `[Context: ...]` cue prepended to chat
messages. That was a label swap, not vertical realism.

This module replaces that with a real registry. Every surface that needs
to adapt per industry — chat suggestions, owner-phone label, owner-SMS
templates, conversation summary verbs, portal terminology, LLM system
prompt — reads from here. Adding industry #13 is a single dict entry,
not a hunt across six files.

Tier allocation (controls depth, not visibility — every industry gets
correct vocabulary and authentic system prompt):

  Tier 1  first-class : HVAC · Real Estate · Septic
  Tier 2  production  : Construction · Electrical · Plumbing ·
                        Property Management · Roofing
  Tier 3  ready       : Landscaping · Legal Intake · Med Spa ·
                        Restoration

Scenario seeds (the demo personas with transcript turns) live in
`src/demo_seed.py` and reference industries by slug. The registry is
metadata + prompts; demo_seed is per-industry persona data. The two
files couple via the slug.

Public API:

    industries.get(slug)              -> dict | None
    industries.list_all()             -> list[dict] (Tier 1 first)
    industries.list_slugs()           -> list[str]
    industries.prompt_fragment(slug)  -> str    (LLM system context)
    industries.owner_sms(slug, kind, ctx) -> str (rendered template)
    industries.suggestions(slug)      -> list[str]
    industries.emergency_keywords(slug) -> list[str]
    industries.portal_term(slug, key) -> str
"""
from __future__ import annotations

from typing import Optional


# ─────────────────────────────────────────────────────────────────────
# TIER 1 — first-class verticals
# ─────────────────────────────────────────────────────────────────────


_HVAC = {
    "slug":               "hvac",
    "tier":               1,
    "name":               "Sunrise HVAC",
    "owner_label":        "Mike",
    "owner_role":         "owner",
    # V11.1 — operational notification label on the owner phone, replacing
    # the pre-V11.1 "{owner_label}'s phone" pattern (felt too personal-
    # demo, broke scalability illusion). Defaults to "Owner"; property
    # management uses "Manager".
    "notification_label": "Owner",
    "customer_term":      "homeowner",
    "business_noun":      "service call",
    "business_noun_plural": "service calls",
    "suggestions": [
        "My AC stopped working and the house is hot",
        "Furnace went out overnight",
        "Need a tune-up before summer",
        "Thermostat isn't talking to the system",
        "What does an estimate cost?",
    ],
    "suggestion_labels": [
        "AC out",
        "No heat",
        "Tune-up",
        "Thermostat",
        "Estimate",
    ],
    "emergency_keywords": [
        "no heat", "no ac", "no a/c", "no air", "no cooling",
        "freezing", "furnace died", "ac died", "ac stopped",
        "gas smell", "carbon monoxide", "co alarm",
        "burning smell from vents", "smoke",
    ],
    "emergency_indicator": "Emergency",
    "owner_sms_templates": {
        # V13.0 — body-only. The customer's name is now rendered as the
        # bubble sender (with their avatar) so repeating it in the body
        # is redundant. The urgent class on the bubble conveys
        # "Emergency" visually; the body just carries the situational
        # details a real receptionist would text.
        "emergency": "{addr} — {issue}. Bridging now.",
        "booking":   "{addr} · {window} · {issue}",
        "quote":     "{addr} — {scope}. Estimate requested.",
        "followup":  "{scope} — callback to {phone}",
    },
    "summary_verbs": {
        "emergency": "transferred",
        "booking":   "scheduled",
        "quote":     "estimate requested",
        "followup":  "callback scheduled",
    },
    "system_prompt": (
        "You're the receptionist for an HVAC shop. Homeowners call about "
        "heating outages in winter, AC failures in summer, comfort issues, "
        "and tune-ups. No heat in cold weather and AC out with vulnerable "
        "household members (babies, elderly, pets) are emergencies — "
        "confirm the address on file, flag urgency, get the owner on the "
        "line within 20 minutes. For tune-ups and quotes, offer to schedule "
        "an in-home estimate; don't quote firm prices since system condition "
        "varies. Refrigerant smells, gas smells, or strange noises in the "
        "furnace warrant immediate dispatch. Returning customers shouldn't "
        "be asked for information already on file."
    ),
    "portal_copy": {
        "today_headline":  "Today's calls",
        "recent_label":    "Recent activity",
        "followup_label":  "Worth a follow-up",
        "partner_term":    "customer",
        "stat_calls":      "Service calls",
        "stat_emergencies": "Emergencies",
    },
    # V11.0 — pre-baked owner-phone SMS bubbles. The combined demo's
    # initial render uses these so the owner-phone shows vertical-
    # appropriate "already happened" notifications, not the septic-
    # themed boilerplate from before V11.0. Two entries per industry —
    # one urgent (or near-urgent), one routine.
    "seeded_owner_sms": [
        {"kind": "emergency", "urgent": True,
         "customer_name": "Marcus Reilly",
         "customer_phone": "+15550102001",
         "body": "4729 Maple — AC out, baby in house. "
                 "Bridging now.",
         "ts_label": "6h ago"},
        {"kind": "booking", "urgent": False,
         "customer_name": "Wendy Larsen",
         "customer_phone": "+15550102002",
         "body": "218 Linden · Thursday 1pm furnace tune-up.",
         "ts_label": "yesterday"},
    ],
}


_REAL_ESTATE = {
    "slug":               "real_estate",
    "tier":               1,
    "name":               "Lawrence Realty",
    "owner_label":        "Lauren",
    "owner_role":         "agent",
    "notification_label": "Owner",
    "customer_term":      "buyer",
    "business_noun":      "showing",
    "business_noun_plural": "showings",
    "suggestions": [
        "I saw the Birch Road listing — is it still available?",
        "Can I tour 1100 Birch this weekend?",
        "Looking to list my house — what's your commission?",
        "What's the asking price on Birch Road?",
        "I'm at a showing and the lockbox isn't working",
        "Can you send me the disclosure?",
    ],
    "suggestion_labels": [
        "Listing inquiry",
        "Tour Saturday",
        "List my house",
        "Asking price",
        "Lockbox stuck",
        "Disclosure",
    ],
    "emergency_keywords": [
        "locked out", "lockbox", "stuck at the showing",
        "stuck inside", "stuck outside the property",
        "can't get in", "alarm going off",
    ],
    "emergency_indicator": "Active showing",
    "owner_sms_templates": {
        # V13.0 — body-only (sender = customer name + avatar).
        "emergency": "{addr} — lockbox stuck. Buyer on-site at {phone}.",
        "booking":   "{addr} · {time}. Callback {phone}.",
        "quote":     "{addr} — wants to list. CMA prep at {phone}.",
        "followup":  "{addr} — buyer follow-up at {phone}.",
    },
    "summary_verbs": {
        "emergency": "agent paged",
        "booking":   "showing scheduled",
        "quote":     "CMA requested",
        "followup":  "callback scheduled",
    },
    "system_prompt": (
        "You're the receptionist for a real-estate brokerage. Most callers "
        "are buyers asking about listings, scheduling showings, or following "
        "up after an open house. Speed-to-lead matters — buyers shop multiple "
        "agents simultaneously, and the agent's conversion rate jumps "
        "significantly when contact happens within 5 minutes. Capture: their "
        "name, callback number, which listing they're asking about, and "
        "whether they want a tour or just details. Offer weekend showing "
        "slots (Saturday or Sunday afternoon) when they ask. Don't quote "
        "market value, comparable sales, or negotiation strategy — that's "
        "the agent's job. For seller inquiries (someone wanting to list), "
        "flag for a callback with CMA prep. Lockbox or access issues at "
        "active showings are emergencies — text the agent immediately."
    ),
    "portal_copy": {
        "today_headline":  "Today's leads",
        "recent_label":    "Recent inquiries",
        "followup_label":  "Worth a follow-up",
        "partner_term":    "lead",
        "stat_calls":      "Inquiries",
        "stat_emergencies": "Active showings",
    },
    "seeded_owner_sms": [
        {"kind": "emergency", "urgent": True,
         "customer_name": "Jordan Bailey",
         "customer_phone": "+15550103005",
         "body": "1100 Birch — lockbox stuck. Buyer on-site now.",
         "ts_label": "15m ago"},
        {"kind": "booking", "urgent": False,
         "customer_name": "Caleb Morrison",
         "customer_phone": "+15550103001",
         "body": "1100 Birch · Saturday 1pm. New buyer.",
         "ts_label": "1h ago"},
    ],
}


_SEPTIC = {
    "slug":               "septic",
    "tier":               1,
    "name":               "Septic Pro",
    "owner_label":        "Bob",
    "owner_role":         "owner",
    "notification_label": "Owner",
    "customer_term":      "homeowner",
    "business_noun":      "service call",
    "business_noun_plural": "service calls",
    "suggestions": [
        "My toilets are backing up — basement is wet",
        "Need to schedule a routine pump-out",
        "How much does a pump-out cost?",
        "Looking for a quote on a new drain field",
        "Septic alarm is beeping",
    ],
    "suggestion_labels": [
        "Sewage backup",
        "Pump-out",
        "Pricing",
        "Drain field",
        "Tank alarm",
    ],
    "emergency_keywords": [
        "backing up", "backup", "sewage", "overflow", "overflowing",
        "septic alarm", "tank alarm", "smell of sewage",
        "drain field flooded",
    ],
    "emergency_indicator": "Emergency",
    "owner_sms_templates": {
        # V13.0 — body-only (sender = customer name + avatar).
        "emergency": "{addr} — {issue}. Bridging now.",
        "booking":   "{addr} · pump-out {window}.",
        "quote":     "{addr} — {scope}. Estimate requested.",
        "followup":  "{scope} — callback to {phone}",
    },
    "summary_verbs": {
        "emergency": "transferred",
        "booking":   "pump-out scheduled",
        "quote":     "quote requested",
        "followup":  "callback scheduled",
    },
    "system_prompt": (
        "You're the receptionist for a septic services company. Homeowners "
        "call about pump-outs, drain field issues, inspections, and "
        "emergencies. Sewage backups, overflowing toilets, and septic-tank "
        "alarms are emergencies — capture the address and flag for immediate "
        "dispatch. For routine pump-outs, offer in-area weekday slots — "
        "homeowner-side scheduling is flexible. Quotes for new drain fields "
        "or system replacement should route to the owner for a callback — "
        "don't ballpark major-job pricing. Returning customers' tank sizes "
        "and last service dates are on file; don't ask twice."
    ),
    "portal_copy": {
        "today_headline":  "Today's calls",
        "recent_label":    "Recent activity",
        "followup_label":  "Worth a follow-up",
        "partner_term":    "customer",
        "stat_calls":      "Service calls",
        "stat_emergencies": "Emergencies",
    },
    "seeded_owner_sms": [
        {"kind": "emergency", "urgent": True,
         # V13.0 C — distinct septic persona; previously shared
         # "Marcus Reilly" with HVAC and read as data duplication
         # during HVAC→Septic demo switching.
         "customer_name": "Henry Walsh",
         "customer_phone": "+15550101001",
         "body": "412 Maple — sewage backup in basement. "
                 "Bridging now.",
         "ts_label": "6h ago"},
        {"kind": "booking", "urgent": False,
         "customer_name": "Sarah Wong",
         "customer_phone": "+15550101002",
         "body": "412 Oak · Tuesday 1pm pump-out.",
         "ts_label": "yesterday"},
    ],
}


# ─────────────────────────────────────────────────────────────────────
# TIER 2 — production-grade verticals
# ─────────────────────────────────────────────────────────────────────


_PLUMBING = {
    "slug":               "plumbing",
    "tier":               2,
    "name":               "Riverside Plumbing",
    "owner_label":        "Dave",
    "owner_role":         "owner",
    "notification_label": "Owner",
    "customer_term":      "homeowner",
    "business_noun":      "service call",
    "business_noun_plural": "service calls",
    "suggestions": [
        "I've got water leaking under the sink",
        "Water heater stopped working",
        "Drain is completely backed up",
        "Need a quote to replace a toilet",
        "Outdoor spigot is broken",
    ],
    "suggestion_labels": [
        "Leak",
        "Water heater",
        "Drain clog",
        "Toilet swap",
        "Spigot",
    ],
    "emergency_keywords": [
        "burst pipe", "pipe burst", "flooding", "water everywhere",
        "no water", "gas leak", "sewage backup", "geyser",
        "water heater leaking", "main shut off",
    ],
    "emergency_indicator": "Active leak",
    "owner_sms_templates": {
        # V13.0 — body-only (sender = customer name + avatar).
        "emergency": "{addr} — {issue}. Tech dispatched.",
        "booking":   "{addr} · {window} · {issue}",
        "quote":     "{addr} — {scope}. Estimate requested.",
        "followup":  "{scope} — callback to {phone}",
    },
    "summary_verbs": {
        "emergency": "tech dispatched",
        "booking":   "scheduled",
        "quote":     "estimate requested",
        "followup":  "callback scheduled",
    },
    "system_prompt": (
        "You're the receptionist for a plumbing company. Homeowners call "
        "about leaks, drain issues, water heater failures, fixture "
        "replacements, and emergencies. Active leaks (especially behind "
        "walls or near electrical), no-water situations, sewer backups, "
        "and gas-line concerns are emergencies — capture the address and "
        "route to the on-call tech immediately. For drain cleaning, "
        "fixture work, and water heater quotes, offer same-day or next-day "
        "scheduling. Don't quote firm prices on jobs that need diagnosis — "
        "offer a service-call visit. Returning customers' addresses are on "
        "file."
    ),
    "portal_copy": {
        "today_headline":  "Today's calls",
        "recent_label":    "Recent activity",
        "followup_label":  "Worth a follow-up",
        "partner_term":    "customer",
        "stat_calls":      "Service calls",
        "stat_emergencies": "Active leaks",
    },
    "seeded_owner_sms": [
        {"kind": "emergency", "urgent": True,
         "customer_name": "Anita Brooks",
         "customer_phone": "+15550104001",
         "body": "319 Cedar — pipe burst behind wall. "
                 "Tech dispatched.",
         "ts_label": "2h ago"},
        {"kind": "booking", "urgent": False,
         "customer_name": "Tony Russo",
         "customer_phone": "+15550104002",
         "body": "1428 Oak Hollow · Tuesday 10am water heater.",
         "ts_label": "yesterday"},
    ],
}


_ROOFING = {
    "slug":               "roofing",
    "tier":               2,
    "name":               "Northstar Roofing",
    "owner_label":        "Ryan",
    "owner_role":         "owner",
    "notification_label": "Owner",
    "customer_term":      "homeowner",
    "business_noun":      "estimate",
    "business_noun_plural": "estimates",
    "suggestions": [
        "Got a leak after the storm last night",
        "Need an inspection — selling the house",
        "Quote for a tear-off and replacement?",
        "Hail damage — working with my insurance",
        "Missing shingles from the wind",
    ],
    "suggestion_labels": [
        "Active leak",
        "Inspection",
        "Replace quote",
        "Hail damage",
        "Missing shingles",
    ],
    "emergency_keywords": [
        "active leak", "leaking now", "water coming through ceiling",
        "ceiling collapsed", "tarp needed", "exposed deck",
        "structural damage",
    ],
    "emergency_indicator": "Active leak",
    "owner_sms_templates": {
        # V13.0 — body-only (sender = customer name + avatar).
        "emergency": "{addr} — active leak. Tarp crew needed.",
        "booking":   "{addr} · inspection {window}.",
        "quote":     "{addr} — {scope}. Estimate requested.",
        "followup":  "{scope} — callback to {phone}",
    },
    "summary_verbs": {
        "emergency": "tarp crew dispatched",
        "booking":   "inspection scheduled",
        "quote":     "estimate requested",
        "followup":  "callback scheduled",
    },
    "system_prompt": (
        "You're the receptionist for a roofing company. Homeowners call "
        "about active leaks (especially during rain), storm damage, missing "
        "shingles, replacement quotes, and insurance work. Active leaks "
        "during current weather are emergencies — capture the address and "
        "route immediately so a tarp crew can be dispatched. For routine "
        "inspections, hail-damage insurance work, and tear-off quotes, "
        "schedule an on-site visit; quotes vary too much by roof size, "
        "pitch, and material to quote over the phone. Insurance-driven "
        "work often needs documentation — note the carrier when mentioned. "
        "Don't quote replacement prices over the phone."
    ),
    "portal_copy": {
        "today_headline":  "Today's calls",
        "recent_label":    "Recent activity",
        "followup_label":  "Worth a follow-up",
        "partner_term":    "customer",
        "stat_calls":      "Estimate requests",
        "stat_emergencies": "Active leaks",
    },
    "seeded_owner_sms": [
        {"kind": "emergency", "urgent": True,
         "customer_name": "Brad Mitchell",
         "customer_phone": "+15550105001",
         "body": "623 Hillcrest — active leak. Tarp crew dispatched.",
         "ts_label": "1h ago"},
        {"kind": "booking", "urgent": False,
         "customer_name": "Lisa Yoon",
         "customer_phone": "+15550105004",
         "body": "388 Magnolia · Wednesday 9am inspection.",
         "ts_label": "6h ago"},
    ],
}


_CONSTRUCTION = {
    "slug":               "construction",
    "tier":               2,
    "name":               "Cornerstone Builders",
    "owner_label":        "Carlos",
    "owner_role":         "owner",
    "notification_label": "Owner",
    "customer_term":      "homeowner",
    "business_noun":      "estimate",
    "business_noun_plural": "estimates",
    "suggestions": [
        "Looking for an estimate on a kitchen remodel",
        "Need a contractor for an addition",
        "Timeline on a basement finish?",
        "Question about permits for a deck",
        "Got drawings ready — when can someone come look?",
    ],
    "suggestion_labels": [
        "Kitchen remodel",
        "Addition",
        "Basement",
        "Permits",
        "Drawings ready",
    ],
    "emergency_keywords": [
        "structural collapse", "site emergency",
        "active job-site issue", "safety incident",
    ],
    "emergency_indicator": "Site issue",
    "owner_sms_templates": {
        # V13.0 — body-only (sender = customer name + avatar).
        "emergency": "{addr} — site issue. {issue}.",
        "booking":   "{addr} · {window} · {scope}",
        "quote":     "{addr} — {scope}. Estimate requested.",
        "followup":  "{scope} — callback to {phone}",
    },
    "summary_verbs": {
        "emergency": "owner paged",
        "booking":   "estimate visit scheduled",
        "quote":     "estimate requested",
        "followup":  "callback scheduled",
    },
    "system_prompt": (
        "You're the receptionist for a general contractor. Callers are "
        "usually homeowners requesting estimates on remodels, additions, "
        "basement finishes, or full-scope projects. Capture: project scope, "
        "address, timeline expectations, and budget range if offered. "
        "Schedule an in-home estimate visit — most contractors offer "
        "complimentary estimates. Subcontractor inquiries (asking about "
        "work-for-hire) should be politely deferred to the owner's "
        "callback. Don't quote project costs over the phone; permits, "
        "materials, and site conditions vary too much. Returning clients' "
        "addresses are on file."
    ),
    "portal_copy": {
        "today_headline":  "Today's calls",
        "recent_label":    "Recent activity",
        "followup_label":  "Worth a follow-up",
        "partner_term":    "client",
        "stat_calls":      "Estimate requests",
        "stat_emergencies": "Site issues",
    },
    "seeded_owner_sms": [
        {"kind": "booking", "urgent": True,
         "customer_name": "Paul Anderson",
         "customer_phone": "+15550106001",
         "body": "927 Crestview · Friday 10am — kitchen remodel walk.",
         "ts_label": "5h ago"},
        {"kind": "quote", "urgent": False,
         "customer_name": "Julia Sanchez",
         "customer_phone": "+15550106002",
         "body": "1503 Beverly · Thursday 2pm — addition walk.",
         "ts_label": "yesterday"},
    ],
}


_PROPERTY_MANAGEMENT = {
    "slug":               "property_management",
    "tier":               2,
    "name":               "Hudson Property Management",
    "owner_label":        "Karen",
    "owner_role":         "manager",
    "notification_label": "Manager",
    "customer_term":      "resident",
    "business_noun":      "request",
    "business_noun_plural": "requests",
    "suggestions": [
        "I'm a tenant — my heat is out",
        "Looking at the unit on 5th Street — still available?",
        "Need to renew my lease — what are my options?",
        "Status on my application?",
        "I locked myself out of the unit",
    ],
    "suggestion_labels": [
        "Tenant: no heat",
        "Unit tour",
        "Lease renewal",
        "Application",
        "Lockout",
    ],
    "emergency_keywords": [
        "no heat", "no water", "no electricity", "active leak",
        "flooding", "locked out", "smoke", "gas smell",
        "fire alarm", "break-in",
    ],
    "emergency_indicator": "Tenant emergency",
    "owner_sms_templates": {
        # V13.0 — body-only (sender = customer name + avatar).
        "emergency": "{unit} — {issue}. On-call maintenance dispatched.",
        "booking":   "{unit} · {time} tour. Callback {phone}.",
        "quote":     "{unit} — application started. Callback {phone}.",
        "followup":  "{unit} — resident follow-up at {phone}.",
    },
    "summary_verbs": {
        "emergency": "maintenance dispatched",
        "booking":   "tour scheduled",
        "quote":     "application started",
        "followup":  "callback scheduled",
    },
    "system_prompt": (
        "You're the receptionist for a property-management company. Two "
        "caller types: tenants of managed properties (issues with heat, "
        "water, appliances, lockouts, lease questions) and prospective "
        "tenants or applicants (asking about availability, scheduling tours, "
        "application status). Tenant emergencies — no heat, water leaks, "
        "lockouts, smoke, gas smell — route to the on-call maintenance "
        "contact immediately. For lease questions, route to the property "
        "manager during business hours. For new applicants, capture unit "
        "interest, callback number, and tour availability. Don't make "
        "commitments on rent, security deposits, or pet policies — those "
        "route to the manager."
    ),
    "portal_copy": {
        "today_headline":  "Today's requests",
        "recent_label":    "Recent activity",
        "followup_label":  "Worth a follow-up",
        "partner_term":    "resident",
        "stat_calls":      "Requests",
        "stat_emergencies": "Tenant emergencies",
    },
    "seeded_owner_sms": [
        {"kind": "emergency", "urgent": True,
         "customer_name": "Jasmine Lee",
         "customer_phone": "+15550107001",
         "body": "Unit 4B / 218 Fifth — no heat, baby in unit. "
                 "Maintenance dispatched.",
         "ts_label": "3h ago"},
        {"kind": "booking", "urgent": False,
         "customer_name": "Alex Pham",
         "customer_phone": "+15550107002",
         "body": "218 Fifth · Saturday 11am — 2BR tour.",
         "ts_label": "yesterday"},
    ],
}


_ELECTRICAL = {
    "slug":               "electrical",
    "tier":               2,
    "name":               "Bright Path Electric",
    "owner_label":        "Steve",
    "owner_role":         "owner",
    "notification_label": "Owner",
    "customer_term":      "homeowner",
    "business_noun":      "service call",
    "business_noun_plural": "service calls",
    "suggestions": [
        "Lost power in half the house",
        "Outlet is sparking",
        "Need a quote on a panel upgrade",
        "Question about installing an EV charger",
        "Generator install — what's the price range?",
    ],
    "suggestion_labels": [
        "Partial outage",
        "Sparking outlet",
        "Panel upgrade",
        "EV charger",
        "Generator",
    ],
    "emergency_keywords": [
        "sparking", "smoke", "burning smell", "panel hot",
        "shock", "shocked", "fire", "exposed wires",
        "outlet burned",
    ],
    "emergency_indicator": "Electrical emergency",
    "owner_sms_templates": {
        # V13.0 — body-only (sender = customer name + avatar).
        "emergency": "{addr} — {issue}. Tech dispatched.",
        "booking":   "{addr} · {window} · {issue}",
        "quote":     "{addr} — {scope}. Estimate requested.",
        "followup":  "{scope} — callback to {phone}",
    },
    "summary_verbs": {
        "emergency": "tech dispatched",
        "booking":   "scheduled",
        "quote":     "estimate requested",
        "followup":  "callback scheduled",
    },
    "system_prompt": (
        "You're the receptionist for an electrical contractor. Callers "
        "report outages, sparking outlets, panel issues, generator "
        "inquiries, and renovation electrical work. Sparking outlets, smoke "
        "smells, burning-plastic odors, full panel outages, and electrical "
        "fire risk are emergencies — capture the address and route "
        "immediately. For non-emergency work (panel upgrades, generator "
        "installs, EV chargers, lighting), schedule an in-home estimate. "
        "Don't quote work that requires permit pulls or load calculations. "
        "Returning customers' addresses and previous work are on file."
    ),
    "portal_copy": {
        "today_headline":  "Today's calls",
        "recent_label":    "Recent activity",
        "followup_label":  "Worth a follow-up",
        "partner_term":    "customer",
        "stat_calls":      "Service calls",
        "stat_emergencies": "Emergencies",
    },
    "seeded_owner_sms": [
        {"kind": "emergency", "urgent": True,
         "customer_name": "Nina Castro",
         "customer_phone": "+15550108002",
         "body": "682 Willowbrook — sparking outlet. Tech dispatched.",
         "ts_label": "30m ago"},
        {"kind": "booking", "urgent": False,
         "customer_name": "Steve Whitman",
         "customer_phone": "+15550108003",
         "body": "2218 Walnut Hollow · Friday 10am — panel upgrade.",
         "ts_label": "yesterday"},
    ],
}


# ─────────────────────────────────────────────────────────────────────
# TIER 3 — placeholder-killing verticals
# ─────────────────────────────────────────────────────────────────────


_LANDSCAPING = {
    "slug":               "landscaping",
    "tier":               3,
    "name":               "Greenleaf Landscaping",
    "owner_label":        "Jen",
    "owner_role":         "owner",
    "notification_label": "Owner",
    "customer_term":      "homeowner",
    "business_noun":      "estimate",
    "business_noun_plural": "estimates",
    "suggestions": [
        "Looking for weekly lawn service",
        "Want a quote on a new patio",
        "Snow removal contract for winter?",
        "Tree came down — can someone clear it?",
        "Spring cleanup — what's it cost?",
    ],
    "suggestion_labels": [
        "Weekly lawn",
        "Patio quote",
        "Snow removal",
        "Tree down",
        "Spring cleanup",
    ],
    "emergency_keywords": [
        "tree down", "tree on house", "tree on car",
        "branches blocking driveway", "branches on the roof",
    ],
    "emergency_indicator": "Tree down",
    "owner_sms_templates": {
        # V13.0 — body-only (sender = customer name + avatar).
        "emergency": "{addr} — tree down. {issue}.",
        "booking":   "{addr} · {window} · {scope}",
        "quote":     "{addr} — {scope}. Estimate requested.",
        "followup":  "{scope} — callback to {phone}",
    },
    "summary_verbs": {
        "emergency": "crew dispatched",
        "booking":   "scheduled",
        "quote":     "estimate requested",
        "followup":  "callback scheduled",
    },
    "system_prompt": (
        "You're the receptionist for a landscaping company. Calls are "
        "seasonal — spring cleanups, weekly mowing contracts, fall leaf "
        "removal, snow-removal contracts in winter, plus larger design or "
        "installation projects year-round. Capture: property address, scope "
        "of work, and timeline. For weekly or seasonal contracts, offer a "
        "property-walk to scope and quote. Storm-related calls (downed "
        "trees, branches on roofs or driveways) get faster turnaround but "
        "aren't life-safety emergencies. Don't quote design or installation "
        "projects over the phone — those need an on-site consultation."
    ),
    "portal_copy": {
        "today_headline":  "Today's calls",
        "recent_label":    "Recent activity",
        "followup_label":  "Worth a follow-up",
        "partner_term":    "customer",
        "stat_calls":      "Estimate requests",
        "stat_emergencies": "Storm calls",
    },
    "seeded_owner_sms": [
        {"kind": "emergency", "urgent": True,
         "customer_name": "Chris Boyd",
         "customer_phone": "+15550109002",
         "body": "1840 Forest Edge — tree down across driveway. "
                 "Crew dispatched.",
         "ts_label": "2h ago"},
        {"kind": "booking", "urgent": False,
         "customer_name": "Helen Vargas",
         "customer_phone": "+15550109001",
         "body": "412 Garden View · Saturday 10am — weekly lawn quote.",
         "ts_label": "5h ago"},
    ],
}


_RESTORATION = {
    "slug":               "restoration",
    "tier":               3,
    "name":               "Restore One",
    "owner_label":        "Tom",
    "owner_role":         "owner",
    "notification_label": "Owner",
    "customer_term":      "homeowner",
    "business_noun":      "mitigation",
    "business_noun_plural": "mitigations",
    "suggestions": [
        "My basement is flooded",
        "Pipe burst — need cleanup",
        "Water damage from the storm",
        "Insurance is asking for an estimate",
        "Smoke damage from a small fire",
    ],
    "suggestion_labels": [
        "Basement flood",
        "Pipe burst",
        "Storm damage",
        "Insurance",
        "Smoke damage",
    ],
    "emergency_keywords": [
        "flooded", "flooding", "water everywhere", "burst pipe",
        "sewage backup", "fire damage", "smoke damage",
        "active water", "still actively leaking",
    ],
    "emergency_indicator": "Active water",
    "owner_sms_templates": {
        # V13.0 — body-only (sender = customer name + avatar).
        "emergency": "{addr} — {issue}. Crew within the hour.",
        "booking":   "{addr} · {window} mitigation.",
        "quote":     "{addr} — {scope}. Estimate requested.",
        "followup":  "Claim {claim} — callback to {phone}",
    },
    "summary_verbs": {
        "emergency": "crew dispatched",
        "booking":   "mitigation scheduled",
        "quote":     "estimate requested",
        "followup":  "callback scheduled",
    },
    "system_prompt": (
        "You're the receptionist for a water-damage and restoration company. "
        "Almost every call is urgent — damage worsens by the hour. Callers "
        "report flooding, burst pipes, sewer backups, fire and smoke damage, "
        "and storm aftermath. Capture: address, source of water or damage, "
        "when it started, whether it's still active. Route every call to "
        "the on-call mitigation tech for dispatch within an hour. Many "
        "calls are insurance-driven; note carrier and claim number when "
        "mentioned. Don't quote restoration costs — scope requires on-site "
        "assessment. Existing claims have project numbers on file."
    ),
    "portal_copy": {
        "today_headline":  "Today's calls",
        "recent_label":    "Recent activity",
        "followup_label":  "Open claims",
        "partner_term":    "customer",
        "stat_calls":      "Mitigations",
        "stat_emergencies": "Active losses",
    },
    "seeded_owner_sms": [
        {"kind": "emergency", "urgent": True,
         "customer_name": "Jacob Cole",
         "customer_phone": "+15550201001",
         "body": "344 Brookside — active flooding. "
                 "Crew within the hour.",
         "ts_label": "30m ago"},
        {"kind": "booking", "urgent": False,
         "customer_name": "Naomi Reyes",
         "customer_phone": "+15550201002",
         "body": "1207 Maple Ridge · mitigation this afternoon · "
                 "Allstate claim.",
         "ts_label": "6h ago"},
    ],
}


_MED_SPA = {
    "slug":               "med_spa",
    "tier":               3,
    "name":               "Aurora Medical Spa",
    "owner_label":        "Dr. Patel",
    "owner_role":         "doctor",
    "notification_label": "Owner",
    "customer_term":      "client",
    "business_noun":      "appointment",
    "business_noun_plural": "appointments",
    "suggestions": [
        "Want to book a Botox consultation",
        "Question about laser hair removal pricing",
        "Need to reschedule my appointment Thursday",
        "Do you do lip filler?",
        "Following up on my treatment last week",
    ],
    "suggestion_labels": [
        "Botox consult",
        "Laser pricing",
        "Reschedule",
        "Filler",
        "Follow-up",
    ],
    "emergency_keywords": [
        "adverse reaction", "swelling won't stop", "severe pain",
        "post-treatment emergency",
    ],
    "emergency_indicator": "Clinical concern",
    "owner_sms_templates": {
        # V13.0 — body-only (sender = customer name + avatar).
        "emergency": "{issue}. Callback requested at {phone}.",
        "booking":   "{treatment} · {time}. Callback {phone}.",
        "quote":     "{treatment} — inquiry. Callback {phone}.",
        "followup":  "{treatment} — follow-up at {phone}",
    },
    "summary_verbs": {
        "emergency": "clinician paged",
        "booking":   "consultation booked",
        "quote":     "inquiry captured",
        "followup":  "post-treatment check",
    },
    "system_prompt": (
        "You're the receptionist for a medical spa. Callers book "
        "consultations and treatments (Botox, fillers, laser hair removal, "
        "microneedling, body contouring), reschedule appointments, ask "
        "about pricing and recovery, and confirm post-treatment care. Tone "
        "is warm, discreet, and unhurried — clients are spending time and "
        "money on themselves and want to feel cared for. For new clients, "
        "capture their treatment interest and offer a complimentary "
        "consultation. Reschedules and cancellations get a quick confirm "
        "and a courteous offer to rebook. Don't quote firm prices or give "
        "medical advice — pricing and recovery details are confirmed at "
        "consultation. Post-treatment concerns about swelling, severe pain, "
        "or adverse reactions route to the clinician immediately. Returning "
        "client preferences are on file."
    ),
    "portal_copy": {
        "today_headline":  "Today's appointments",
        "recent_label":    "Recent activity",
        "followup_label":  "Post-treatment check-ins",
        "partner_term":    "client",
        "stat_calls":      "Appointments",
        "stat_emergencies": "Clinical concerns",
    },
    "seeded_owner_sms": [
        {"kind": "booking", "urgent": False,
         "customer_name": "Olivia Bennett",
         "customer_phone": "+15550202001",
         "body": "Botox consult · Saturday 2pm.",
         "ts_label": "4h ago"},
        {"kind": "followup", "urgent": False,
         "customer_name": "Chen Liu",
         "customer_phone": "+15550202003",
         "body": "Filler appointment moved · Friday 11am.",
         "ts_label": "yesterday"},
    ],
}


_LEGAL_INTAKE = {
    "slug":               "legal_intake",
    "tier":               3,
    "name":               "Lawler & Associates",
    "owner_label":        "David",
    "owner_role":         "attorney",
    "notification_label": "Owner",
    "customer_term":      "caller",
    "business_noun":      "consultation",
    "business_noun_plural": "consultations",
    "suggestions": [
        "Need a consultation about a workplace issue",
        "Looking for help with a contract",
        "Question about my landlord",
        "I was in a car accident",
        "Following up on a matter from last month",
    ],
    "suggestion_labels": [
        "Workplace",
        "Contract",
        "Landlord",
        "Car accident",
        "Existing matter",
    ],
    "emergency_keywords": [
        "court tomorrow", "court today", "arrested",
        "in custody", "deadline tomorrow",
        "served papers", "eviction notice",
    ],
    "emergency_indicator": "Time-sensitive",
    "owner_sms_templates": {
        # V13.0 — body-only (sender = caller name + avatar).
        "emergency": "{category} — deadline {deadline}. Callback {phone}.",
        "booking":   "{category} consultation · {time}. Callback {phone}.",
        "quote":     "{category} — intake captured. Callback {phone}.",
        "followup":  "Existing matter — callback to {phone}",
    },
    "summary_verbs": {
        "emergency": "attorney paged",
        "booking":   "consultation scheduled",
        "quote":     "intake captured",
        "followup":  "callback scheduled",
    },
    "system_prompt": (
        "You're the intake receptionist for a law firm. Callers are often "
        "in distress — workplace issues, accidents, contract disputes, "
        "family matters, immigration questions. Your job is to screen for "
        "case category, capture basic contact info, and schedule a "
        "consultation with the attorney. DO NOT take case details over the "
        "phone — statements aren't privileged until the attorney-client "
        "relationship is formed. Use general categories: 'workplace issue,' "
        "'personal injury,' 'family law,' 'business matter,' 'landlord-"
        "tenant.' Don't probe further. Be calm, warm, and unhurried. "
        "Capture: name, callback number, general matter category, urgency "
        "(court dates or deadlines noted). Consultation fees and scheduling "
        "are confirmed by the attorney's office, not by you. Time-sensitive "
        "items (imminent court dates, served papers) route to the attorney "
        "immediately."
    ),
    "portal_copy": {
        "today_headline":  "Today's intakes",
        "recent_label":    "Recent intakes",
        "followup_label":  "Open matters",
        "partner_term":    "caller",
        "stat_calls":      "Intakes",
        "stat_emergencies": "Time-sensitive",
    },
    "seeded_owner_sms": [
        {"kind": "emergency", "urgent": True,
         "customer_name": "Renata Cruz",
         "customer_phone": "+15550203003",
         "body": "Eviction — court in 6 days. Callback today.",
         "ts_label": "2h ago"},
        {"kind": "booking", "urgent": False,
         "customer_name": "Anita Powell",
         "customer_phone": "+15550203001",
         "body": "Workplace matter consultation · Friday 3pm.",
         "ts_label": "6h ago"},
    ],
}


# ─────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────


INDUSTRIES: dict[str, dict] = {
    # Tier 1 — first-class
    _HVAC["slug"]:                _HVAC,
    _REAL_ESTATE["slug"]:         _REAL_ESTATE,
    _SEPTIC["slug"]:              _SEPTIC,
    # Tier 2 — production-grade
    _PLUMBING["slug"]:            _PLUMBING,
    _ROOFING["slug"]:             _ROOFING,
    _CONSTRUCTION["slug"]:        _CONSTRUCTION,
    _PROPERTY_MANAGEMENT["slug"]: _PROPERTY_MANAGEMENT,
    _ELECTRICAL["slug"]:          _ELECTRICAL,
    # Tier 3 — placeholder-killing
    _LANDSCAPING["slug"]:         _LANDSCAPING,
    _RESTORATION["slug"]:         _RESTORATION,
    _MED_SPA["slug"]:             _MED_SPA,
    _LEGAL_INTAKE["slug"]:        _LEGAL_INTAKE,
}


# Display order in the demo switcher. Tier 1 first (HVAC default — the
# emergency moment demos strongest, per the V11.0 plan), then Real Estate
# and Septic, then Tier 2 alphabetical, then Tier 3 alphabetical.
_DISPLAY_ORDER: list[str] = [
    "hvac",
    "real_estate",
    "septic",
    "construction",
    "electrical",
    "plumbing",
    "property_management",
    "roofing",
    "landscaping",
    "legal_intake",
    "med_spa",
    "restoration",
]


# Legacy slug aliases — callers using the pre-V11.0 cue keep working.
_SLUG_ALIASES: dict[str, str] = {
    "real-estate": "real_estate",
    "realty":      "real_estate",
    "realestate":  "real_estate",
    "real estate": "real_estate",
    "property":    "property_management",
    "pm":          "property_management",
    "med-spa":     "med_spa",
    "medspa":      "med_spa",
    "spa":         "med_spa",
    "legal":       "legal_intake",
    "law":         "legal_intake",
    "water_damage": "restoration",
}


def _resolve_slug(slug: str) -> str:
    """Normalize a slug — lowercase, alias-resolved. Returns "" if the
    input is empty so callers can treat that as "no industry selected"."""
    if not slug:
        return ""
    s = slug.strip().lower().replace("-", "_")
    if s in INDUSTRIES:
        return s
    return _SLUG_ALIASES.get(s, _SLUG_ALIASES.get(slug.strip().lower(), s))


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────


def get(slug: str) -> Optional[dict]:
    """Get the industry config by slug. Returns None for unknown slugs.

    Legacy slugs ("real-estate", "realty") are resolved to canonical
    snake_case slugs ("real_estate") for backwards compatibility with the
    pre-V11.0 tenant switcher."""
    resolved = _resolve_slug(slug)
    return INDUSTRIES.get(resolved)


def list_all() -> list[dict]:
    """All industries in display order (Tier 1 first)."""
    return [INDUSTRIES[s] for s in _DISPLAY_ORDER if s in INDUSTRIES]


def list_slugs() -> list[str]:
    """All canonical slugs in display order."""
    return [s for s in _DISPLAY_ORDER if s in INDUSTRIES]


def prompt_fragment(slug: str) -> str:
    """The LLM system prompt fragment for this industry, wrapped in the
    same `[Context: ...] ... Do NOT mention this context in your reply.`
    bracketing the pre-V11.0 cue used. Returns "" for unknown slugs (the
    caller should fall back to the tenant's own system prompt)."""
    ind = get(slug)
    if not ind:
        return ""
    body = ind.get("system_prompt", "").strip()
    if not body:
        return ""
    return (
        "[Context: " + body
        + " Do NOT mention this context in your reply.] "
    )


def owner_sms(slug: str, kind: str,
              ctx: Optional[dict] = None) -> str:
    """Render an owner-SMS template for this industry.

    `kind` is one of: 'emergency' | 'booking' | 'quote' | 'followup'.
    `ctx` provides template variables (name, addr, time, issue, ...).
    Missing variables render as empty placeholders rather than raising,
    so a caller missing one field still gets a usable message.

    Returns "" if the slug or kind isn't found."""
    ind = get(slug)
    if not ind:
        return ""
    templates = ind.get("owner_sms_templates") or {}
    tmpl = templates.get(kind)
    if not tmpl:
        return ""
    # Tolerant rendering — missing keys become empty, never KeyError.
    class _SafeDict(dict):
        def __missing__(self, key):
            return ""
    safe_ctx = _SafeDict(ctx or {})
    try:
        return tmpl.format_map(safe_ctx)
    except Exception:
        return tmpl


def suggestions(slug: str) -> list[str]:
    """The chat suggestion chip strings for this industry. Returns []
    for unknown slugs."""
    ind = get(slug)
    return list(ind.get("suggestions") or []) if ind else []


def suggestion_labels(slug: str) -> list[str]:
    """Short chip-button labels parallel to `suggestions(slug)`. Same
    length, same order. The label shows on the button face; the
    matching suggestion is sent on click."""
    ind = get(slug)
    return list(ind.get("suggestion_labels") or []) if ind else []


def seeded_owner_sms(slug: str) -> list[dict]:
    """Pre-baked owner-phone SMS bubbles for this industry — two
    entries each (emergency/urgent + routine), shown as the initial
    state of the owner phone in the combined demo. Each entry has
    keys: kind, urgent (bool), body, ts_label. Returns [] for unknown
    slugs (which falls back to whatever the demo shipped with)."""
    ind = get(slug)
    return list(ind.get("seeded_owner_sms") or []) if ind else []


def emergency_keywords(slug: str) -> list[str]:
    """Emergency trigger phrases for this industry (lowercase). Returns
    [] for unknown slugs."""
    ind = get(slug)
    return list(ind.get("emergency_keywords") or []) if ind else []


def portal_term(slug: str, key: str, default: str = "") -> str:
    """Look up a portal terminology string for this industry. Falls back
    to `default` (or "") when the slug or key isn't found.

    Known keys: 'today_headline', 'recent_label', 'followup_label',
    'partner_term', 'stat_calls', 'stat_emergencies'."""
    ind = get(slug)
    if not ind:
        return default
    return (ind.get("portal_copy") or {}).get(key, default)


def summary_verb(slug: str, kind: str, default: str = "") -> str:
    """Look up the summary verb for a conversation outcome. Used when
    rendering call cards so the verb matches the vertical (e.g.,
    'showing scheduled' for real estate vs. 'pump-out scheduled' for
    septic).

    `kind` is one of: 'emergency' | 'booking' | 'quote' | 'followup'."""
    ind = get(slug)
    if not ind:
        return default
    return (ind.get("summary_verbs") or {}).get(kind, default)


def owner_label(slug: str, default: str = "the owner") -> str:
    """Owner first name for this industry's demo brand."""
    ind = get(slug)
    return ind.get("owner_label", default) if ind else default


def notification_label(slug: str, default: str = "Owner") -> str:
    """V11.1 — operational notification label shown on the owner phone
    bar (e.g., 'Owner', 'Manager'). Replaces the pre-V11.1 pattern of
    `{owner_label}'s phone` which felt too personal-demo and broke the
    illusion of a scalable product. Defaults to 'Owner' for unknown
    slugs."""
    ind = get(slug)
    return ind.get("notification_label", default) if ind else default


def brand_name(slug: str, default: str = "this service") -> str:
    """Display brand name for this industry's demo tenant."""
    ind = get(slug)
    return ind.get("name", default) if ind else default


def emergency_indicator(slug: str, default: str = "Emergency") -> str:
    """The urgency-label this vertical uses in UI (e.g., 'Active leak'
    for plumbing, 'Active showing' for real estate). Defaults to
    'Emergency' for unknown slugs."""
    ind = get(slug)
    return ind.get("emergency_indicator", default) if ind else default
